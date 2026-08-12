"""Deterministic read-only health checks for Pangu engineering dependencies."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from pangu.config import ConfigSnapshot
from pangu.health.contracts import HealthComponent
from pangu.observability import EngineeringEventStore, PanguLogger
from pangu.version import get_version_manifest
from pangu.version.factor_versions import PRODUCTION_FACTOR_VERSION
from pangu.version.strategy_versions import PRODUCTION_STRATEGY_VERSION


def _status(score: float) -> str:
    if score >= 85:
        return "HEALTHY"
    if score >= 60:
        return "DEGRADED"
    return "UNHEALTHY"


class EngineeringHealthService:
    """Evaluate data, databases, AI and strategy without changing any domain state."""

    def __init__(
        self,
        project_root: Path,
        config: ConfigSnapshot,
        store: EngineeringEventStore,
        logger: PanguLogger,
        model_status_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(project_root))
        self.config = config
        self.store = store
        self.logger = logger
        self.model_status_provider = model_status_provider

    def evaluate(self, *, persist: bool = True, trace_id: str | None = None) -> dict[str, Any]:
        """Run all read-only checks and optionally persist one immutable result."""
        trace = trace_id or str(uuid4())
        checked_at = datetime.now(timezone.utc).isoformat()
        checks = {
            "data": self._check_data(),
            "database": self._check_databases(),
            "ai": self._check_ai(),
            "strategy": self._check_strategy(),
        }
        weights = dict(self.config.configuration["engineering"]["health_weights"])
        score = round(sum(checks[name].score * float(weights[name]) / 100 for name in checks), 2)
        payload: dict[str, Any] = {
            "health_id": f"health-{checked_at[:10]}-{uuid4().hex[:12]}",
            "checked_at": checked_at,
            "status": _status(score),
            "score": score,
            "weights": weights,
            "components": {name: item.to_dict() for name, item in checks.items()},
            "config_hash": self.config.config_hash,
            "version_manifest_hash": get_version_manifest().manifest_hash,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_risk": False,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["evidence_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if persist:
            self.store.save_health(payload)
            self.logger.event(
                "INFO" if payload["status"] == "HEALTHY" else "WARNING",
                "health_center",
                "engineering_health_evaluated",
                f"工程健康检查完成：{payload['status']} {score}",
                trace_id=trace,
                extra={"health_id": payload["health_id"], "score": score, "status": payload["status"]},
            )
        return payload

    def latest(self) -> dict[str, Any] | None:
        """Return the latest persisted health snapshot without evaluating again."""
        rows = self.store.list_health(1)
        if not rows:
            return None
        payload = rows[0]
        payload.update(
            {
                "weights": dict(self.config.configuration["engineering"]["health_weights"]),
                "config_hash": self.config.config_hash,
                "version_manifest_hash": get_version_manifest().manifest_hash,
                "can_trade": False,
                "can_create_orders": False,
                "can_modify_strategy": False,
                "can_modify_risk": False,
            }
        )
        return payload

    def _check_data(self) -> HealthComponent:
        center = self.root / "output" / "data_center"
        catalog = center / "catalog.sqlite3"
        latest = center / "latest.json"
        gaps: list[str] = []
        evidence: dict[str, Any] = {
            "data_center_root": "output/data_center",
            "catalog_exists": catalog.is_file(),
            "latest_manifest_pointer_exists": latest.is_file(),
        }
        if not catalog.is_file():
            return HealthComponent("data", "DEGRADED", 60, "尚无 Data Center 目录或目录数据库", evidence, ("catalog_missing",))
        try:
            with closing(sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True, timeout=3)) as connection:
                integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                run_count = int(connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0])
            evidence.update({"catalog_quick_check": integrity, "research_run_count": run_count})
            if integrity != "ok":
                return HealthComponent("data", "UNHEALTHY", 20, "Data Center 目录数据库完整性异常", evidence)
        except (sqlite3.Error, OSError) as exc:
            evidence["error"] = type(exc).__name__
            return HealthComponent("data", "UNHEALTHY", 0, "Data Center 无法只读验证", evidence)
        if latest.is_file():
            try:
                pointer = json.loads(latest.read_text(encoding="utf-8"))
                evidence["latest_run_id"] = pointer.get("run_id")
                evidence["latest_research_date"] = pointer.get("research_date")
            except (OSError, json.JSONDecodeError):
                gaps.append("latest_pointer_invalid")
        else:
            gaps.append("latest_pointer_missing")
        score = 100 if not gaps and evidence.get("research_run_count", 0) > 0 else 80
        return HealthComponent("data", _status(score), score, "Data Center 可只读验证", evidence, tuple(gaps))

    def _check_databases(self) -> HealthComponent:
        output = self.root / "output"
        candidates = sorted(
            path for path in output.rglob("*")
            if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            and "backups" not in path.parts
        )
        details: list[dict[str, Any]] = []
        failed = 0
        for path in candidates:
            item = {"path": path.relative_to(self.root).as_posix()}
            try:
                with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)) as connection:
                    item["quick_check"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                if item["quick_check"] != "ok":
                    failed += 1
            except (sqlite3.Error, OSError) as exc:
                failed += 1
                item["quick_check"] = "error"
                item["error"] = type(exc).__name__
            details.append(item)
        if not candidates:
            return HealthComponent("database", "DEGRADED", 60, "尚无业务数据库可检查", {"databases": []}, ("database_missing",))
        score = max(0.0, round(100 * (len(candidates) - failed) / len(candidates), 2))
        return HealthComponent(
            "database", _status(score), score,
            "数据库只读完整性检查完成" if not failed else f"{failed} 个数据库检查失败",
            {"database_count": len(candidates), "failed_count": failed, "databases": details},
        )

    def _check_ai(self) -> HealthComponent:
        if self.model_status_provider is None:
            return HealthComponent("ai", "DEGRADED", 70, "AI 状态提供器未注入；AI 为可选依赖", {"state": "not_checked"}, ("provider_not_injected",))
        try:
            public = dict(self.model_status_provider())
        except Exception as exc:  # Boundary: third-party provider status is isolated here.
            return HealthComponent("ai", "UNHEALTHY", 0, "AI 状态读取失败", {"state": "error", "error": type(exc).__name__})
        public.pop("api_key", None)
        state = str(public.get("state") or "unknown").lower()
        if state in {"connected", "ready", "healthy"}:
            score = 100
        elif state in {"disabled", "not_configured", "unknown"}:
            score = 70
        else:
            score = 30
        return HealthComponent("ai", _status(score), score, f"AI 状态：{state}", public)

    def _check_strategy(self) -> HealthComponent:
        try:
            from ashare_agent.factor_model import FACTOR_MODEL_VERSION
            from ashare_agent.research_pipeline import STRATEGY_VERSION
        except ImportError as exc:
            return HealthComponent("strategy", "UNHEALTHY", 0, "无法导入生产策略合同", {"error": type(exc).__name__})
        evidence = {
            "runtime_strategy_version": STRATEGY_VERSION,
            "expected_strategy_version": PRODUCTION_STRATEGY_VERSION,
            "runtime_factor_version": FACTOR_MODEL_VERSION,
            "expected_factor_version": PRODUCTION_FACTOR_VERSION,
        }
        drift = STRATEGY_VERSION != PRODUCTION_STRATEGY_VERSION or FACTOR_MODEL_VERSION != PRODUCTION_FACTOR_VERSION
        if drift:
            return HealthComponent("strategy", "UNHEALTHY", 0, "策略或因子版本与统一版本中心漂移", evidence)
        latest = self.root / "output" / "data_center" / "latest.json"
        if not latest.is_file():
            return HealthComponent("strategy", "DEGRADED", 80, "版本合同一致，但尚无最新研究快照", evidence, ("latest_research_snapshot_missing",))
        return HealthComponent("strategy", "HEALTHY", 100, "策略与因子版本合同一致", evidence)
