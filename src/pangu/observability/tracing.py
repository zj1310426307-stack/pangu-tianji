"""Local trace/span lifecycle with redacted bounded attributes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from time import perf_counter
from typing import Any, Iterator

from .context import create_context, current_context, new_id, telemetry_context
from .contracts import TelemetryContext, TelemetryStatus, canonical_hash
from .store import ObservabilityStore


_SENSITIVE = re.compile(r"authorization|cookie|api[_-]?key|password|secret|token|credential|prompt|request_body", re.I)


def _safe(value: Any, key: str = "") -> Any:
    """Remove sensitive/high-volume trace attributes and cap remaining strings."""
    if _SENSITIVE.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in list(value.items())[:30]}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in list(value)[:30]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


class TraceService:
    """Persist immutable completed traces and tree-structured spans."""

    def __init__(self, store: ObservabilityStore) -> None:
        self.store = store

    def start_trace(self, root_operation: str, *, context: TelemetryContext | None = None, linked_ids: dict[str, str] | None = None) -> TelemetryContext:
        """Start one unique root trace and return its bound-ready context."""
        selected = context or create_context(operation_id=root_operation)
        started_at = datetime.now(timezone.utc).isoformat()
        links = _safe(linked_ids or {})
        self.store.execute(
            """INSERT INTO traces(trace_id,root_operation,started_at,status,linked_ids_json)
            VALUES(?,?,?,?,?)""",
            (selected.trace_id, root_operation[:120], started_at, "RUNNING", json.dumps(links, ensure_ascii=False, sort_keys=True)),
        )
        return selected

    def finish_trace(self, trace_id: str, *, status: TelemetryStatus | str = TelemetryStatus.OK, error_code: str | None = None) -> dict[str, Any]:
        """Move a running trace once to an immutable terminal state."""
        trace = self.store.one("SELECT * FROM traces WHERE trace_id=?", (trace_id,))
        if not trace:
            raise ValueError("Trace 不存在")
        if trace["status"] != "RUNNING":
            raise ValueError("已完成 Trace 不可覆盖")
        completed = datetime.now(timezone.utc)
        started = datetime.fromisoformat(trace["started_at"])
        duration = max(0.0, (completed - started).total_seconds() * 1000)
        terminal = TelemetryStatus(status).value
        evidence = canonical_hash({"trace_id": trace_id, "completed_at": completed.isoformat(), "duration_ms": duration, "status": terminal, "error_code": error_code})
        changed = self.store.execute(
            """UPDATE traces SET completed_at=?,duration_ms=?,status=?,error_code=?,evidence_hash=?
            WHERE trace_id=? AND status='RUNNING'""",
            (completed.isoformat(), duration, terminal, error_code, evidence, trace_id),
        )
        if changed != 1:
            raise ValueError("Trace 已由其他执行者结束")
        return self.trace(trace_id) or {}

    def start_span(
        self,
        name: str,
        module: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        """Append one running span after validating its parent belongs to the trace."""
        context = current_context()
        selected_trace = trace_id or (context.trace_id if context else None)
        if not selected_trace or not self.store.one("SELECT trace_id FROM traces WHERE trace_id=?", (selected_trace,)):
            raise ValueError("Span 必须关联已存在 Trace")
        parent = parent_span_id or (context.span_id if context else None)
        if parent:
            row = self.store.one("SELECT trace_id FROM spans WHERE span_id=?", (parent,))
            if not row or row["trace_id"] != selected_trace:
                raise ValueError("父 Span 不属于当前 Trace")
        span_id = new_id("span")
        safe_attributes = _safe(attributes or {})
        self.store.execute(
            """INSERT INTO spans(span_id,trace_id,parent_span_id,name,module,started_at,status,attributes_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                span_id, selected_trace, parent, name[:120], module[:120], datetime.now(timezone.utc).isoformat(),
                "RUNNING", json.dumps(safe_attributes, ensure_ascii=False, sort_keys=True),
            ),
        )
        return span_id

    def finish_span(
        self,
        span_id: str,
        *,
        status: TelemetryStatus | str = TelemetryStatus.OK,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Complete one span once, storing only a redacted bounded error summary."""
        row = self.store.one("SELECT * FROM spans WHERE span_id=?", (span_id,))
        if not row or row["status"] != "RUNNING":
            raise ValueError("Span 不存在或已经结束")
        completed = datetime.now(timezone.utc)
        duration = max(0.0, (completed - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000)
        terminal = TelemetryStatus(status).value
        safe_message = str(_safe(error_message or "", "error_message"))[:800] or None
        evidence = canonical_hash({"span_id": span_id, "status": terminal, "duration_ms": duration, "error_code": error_code})
        changed = self.store.execute(
            """UPDATE spans SET completed_at=?,duration_ms=?,status=?,error_code=?,error_message=?,evidence_hash=?
            WHERE span_id=? AND status='RUNNING'""",
            (completed.isoformat(), duration, terminal, error_code, safe_message, evidence, span_id),
        )
        if changed != 1:
            raise ValueError("Span 已由其他执行者结束")
        return self.store.one("SELECT * FROM spans WHERE span_id=?", (span_id,)) or {}

    @contextmanager
    def span(self, name: str, module: str, *, attributes: dict[str, Any] | None = None) -> Iterator[str]:
        """Trace a service block and preserve error evidence without swallowing it."""
        context = current_context()
        if context is None:
            raise ValueError("span() 需要活动 TelemetryContext")
        span_id = self.start_span(name, module, attributes=attributes)
        child = TelemetryContext(**{**context.to_dict(), "span_id": span_id, "parent_span_id": context.span_id})
        started = perf_counter()
        try:
            with telemetry_context(child):
                yield span_id
            self.finish_span(span_id, status=TelemetryStatus.OK)
        except Exception as exc:
            self.finish_span(span_id, status=TelemetryStatus.ERROR, error_code=type(exc).__name__, error_message=str(exc))
            raise
        finally:
            _ = perf_counter() - started

    def trace(self, trace_id: str) -> dict[str, Any] | None:
        """Read one trace with spans ordered as a reconstructable tree input."""
        root = self.store.one("SELECT * FROM traces WHERE trace_id=?", (trace_id,))
        if not root:
            return None
        root["linked_ids"] = json.loads(root.pop("linked_ids_json"))
        spans = self.store.all("SELECT * FROM spans WHERE trace_id=? ORDER BY started_at,span_id", (trace_id,))
        for span in spans:
            span["attributes"] = json.loads(span.pop("attributes_json"))
        root["spans"] = spans
        root["can_trade"] = False
        root["can_create_orders"] = False
        root["can_auto_remediate"] = False
        return root

    def list_traces(self, *, query: str | None = None, start: str | None = None, end: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List bounded trace headers with optional ID/run correlation search."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if query:
            clauses.append("(trace_id LIKE ? OR linked_ids_json LIKE ? OR root_operation LIKE ?)")
            needle = f"%{query}%"
            parameters.extend((needle, needle, needle))
        if start:
            clauses.append("started_at>=?")
            parameters.append(start)
        if end:
            clauses.append("started_at<=?")
            parameters.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        rows = self.store.all(f"SELECT * FROM traces {where} ORDER BY started_at DESC LIMIT ?", parameters)
        for row in rows:
            row["linked_ids"] = json.loads(row.pop("linked_ids_json"))
        return rows
