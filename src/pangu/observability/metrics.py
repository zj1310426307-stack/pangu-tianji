"""Thread-safe metric recording and deterministic rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import threading
from typing import Any, Iterable

from .context import current_context
from .contracts import MetricSample, MetricType, canonical_hash
from .store import ObservabilityStore


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    """Return an interpolated percentile for a finite sample."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class MetricRegistry:
    """Persist strong metric samples and expose server-side aggregates."""

    def __init__(self, store: ObservabilityStore) -> None:
        self.store = store
        self._write_lock = threading.RLock()

    def record(
        self,
        metric_name: str,
        metric_type: MetricType | str,
        value: float,
        unit: str,
        *,
        labels: dict[str, str] | None = None,
        source: str = "pangu",
        trace_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Validate and append one sample without touching business state."""
        context = current_context()
        sample = MetricSample(
            metric_name=metric_name,
            metric_type=MetricType(metric_type),
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            value=float(value),
            unit=unit,
            labels=labels or {},
            source=source,
            trace_id=trace_id or (context.trace_id if context else None),
        ).to_dict()
        labels_json = json.dumps(sample["labels"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._write_lock:
            sample_id = self.store.insert(
                """INSERT INTO metric_samples(
                metric_name,metric_type,timestamp,value,unit,labels_json,labels_hash,source,trace_id,evidence_hash,
                can_trade,can_create_orders,can_auto_remediate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample["metric_name"], sample["metric_type"], sample["timestamp"], sample["value"],
                    sample["unit"], labels_json, canonical_hash(sample["labels"]), sample["source"],
                    sample["trace_id"], sample["evidence_hash"], 0, 0, 0,
                ),
            )
        return {"sample_id": sample_id, **sample}

    def list_samples(
        self,
        *,
        metric_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Read filtered raw metrics under a fixed row limit."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if metric_name:
            clauses.append("metric_name=?")
            parameters.append(metric_name)
        if start:
            clauses.append("timestamp>=?")
            parameters.append(start)
        if end:
            clauses.append("timestamp<=?")
            parameters.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 2000)))
        rows = self.store.all(f"SELECT * FROM metric_samples {where} ORDER BY timestamp DESC LIMIT ?", parameters)
        for row in rows:
            row["labels"] = json.loads(row.pop("labels_json"))
            row["can_trade"] = False
            row["can_create_orders"] = False
            row["can_auto_remediate"] = False
        return rows

    def summarize(self, values: Iterable[float]) -> dict[str, float | int | None]:
        """Compute count/min/max/mean and p50/p95/p99 on the server."""
        materialized = [float(value) for value in values]
        return {
            "count": len(materialized),
            "minimum": min(materialized) if materialized else None,
            "maximum": max(materialized) if materialized else None,
            "average": sum(materialized) / len(materialized) if materialized else None,
            "p50": _percentile(materialized, 0.50),
            "p95": _percentile(materialized, 0.95),
            "p99": _percentile(materialized, 0.99),
        }

    def metric_summary(self, metric_name: str, *, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        """Aggregate one metric series without exposing raw business payloads."""
        samples = self.list_samples(metric_name=metric_name, start=start, end=end, limit=2000)
        summary = self.summarize(item["value"] for item in samples)
        return {"metric_name": metric_name, "unit": samples[0]["unit"] if samples else None, **summary}
