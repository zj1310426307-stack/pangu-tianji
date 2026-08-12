"""Evidence-aware SLO evaluation with honest insufficient-data semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from .contracts import SLOStatus, canonical_hash
from .metrics import MetricRegistry
from .store import ObservabilityStore


class SLOService:
    """Register versioned SLOs and evaluate only persisted observations."""

    def __init__(self, store: ObservabilityStore, metrics: MetricRegistry, definitions: list[dict[str, Any]], config_hash: str) -> None:
        self.store = store
        self.metrics = metrics
        self.config_hash = config_hash
        self.definitions = [dict(item) for item in definitions]
        self._register()

    def _register(self) -> None:
        """Upsert configuration-owned definitions without changing evaluations."""
        for item in self.definitions:
            self.store.execute(
                """INSERT INTO slo_definitions(slo_id,name,category,window_seconds,target,comparison,minimum_samples,metric_name,enabled,optional_dependency,config_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slo_id) DO UPDATE SET
                name=excluded.name,category=excluded.category,window_seconds=excluded.window_seconds,target=excluded.target,
                comparison=excluded.comparison,minimum_samples=excluded.minimum_samples,metric_name=excluded.metric_name,
                enabled=excluded.enabled,optional_dependency=excluded.optional_dependency,config_hash=excluded.config_hash""",
                (
                    item["slo_id"], item["name"], item["category"], int(item["window_seconds"]), float(item["target"]),
                    item["comparison"], int(item["minimum_samples"]), item.get("metric_name"), int(bool(item.get("enabled", True))),
                    int(bool(item.get("optional_dependency", False))), self.config_hash,
                ),
            )

    def evaluate_all(self, *, ai_enabled: bool = False, now: datetime | None = None) -> list[dict[str, Any]]:
        """Evaluate every enabled definition; optional unused AI is not a failure."""
        return [self.evaluate(item["slo_id"], ai_enabled=ai_enabled, now=now) for item in self.definitions if item.get("enabled", True)]

    def evaluate(self, slo_id: str, *, ai_enabled: bool = False, now: datetime | None = None) -> dict[str, Any]:
        """Evaluate one SLO with fixed target and un-reweighted sample requirements."""
        definition = next((item for item in self.definitions if item["slo_id"] == slo_id), None)
        if not definition:
            raise ValueError("SLO 不存在")
        current = now or datetime.now(timezone.utc)
        start = current - timedelta(seconds=int(definition["window_seconds"]))
        samples = self.metrics.list_samples(
            metric_name=definition.get("metric_name"), start=start.isoformat(), end=current.isoformat(), limit=2000
        )
        if definition.get("optional_dependency") and not ai_enabled and not samples:
            status, actual = SLOStatus.NOT_APPLICABLE, None
        elif len(samples) < int(definition["minimum_samples"]):
            status, actual = SLOStatus.INSUFFICIENT_DATA, None
        else:
            values = [float(item["value"]) for item in samples]
            if "p95" in slo_id:
                actual = self.metrics.summarize(values)["p95"]
            else:
                actual = sum(values) / len(values)
            target = float(definition["target"])
            healthy = actual >= target if definition["comparison"] == "GTE" else actual <= target
            if healthy:
                status = SLOStatus.HEALTHY
            else:
                ratio = (actual / target) if definition["comparison"] == "GTE" and target else (target / actual if actual else 0)
                status = SLOStatus.AT_RISK if ratio >= 0.9 else SLOStatus.BREACHED
        target = float(definition["target"])
        error_budget = None
        if actual is not None:
            error_budget = (actual - target) if definition["comparison"] == "GTE" else (target - actual)
        evidence = {"metric_name": definition.get("metric_name"), "sample_count": len(samples), "config_hash": self.config_hash}
        payload = {
            "evaluation_id": f"slo-{uuid4().hex}", "slo_id": slo_id, "name": definition["name"],
            "window": int(definition["window_seconds"]), "target": target, "actual": actual,
            "status": status.value, "error_budget": error_budget, "sample_count": len(samples),
            "evaluated_at": current.isoformat(), "window_start": start.isoformat(), "window_end": current.isoformat(),
            "evidence": evidence,
        }
        payload["evidence_hash"] = canonical_hash(payload)
        self.store.execute(
            """INSERT INTO slo_evaluations(evaluation_id,slo_id,evaluated_at,window_start,window_end,target,actual,status,error_budget,sample_count,evidence_json,evidence_hash)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                payload["evaluation_id"], slo_id, payload["evaluated_at"], payload["window_start"], payload["window_end"],
                target, actual, status.value, error_budget, len(samples), json.dumps(evidence, ensure_ascii=False, sort_keys=True), payload["evidence_hash"],
            ),
        )
        return payload

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the latest evaluation per SLO."""
        rows = self.store.all(
            """SELECT e.*,d.name,d.category,d.window_seconds AS window FROM slo_evaluations e
            JOIN slo_definitions d ON d.slo_id=e.slo_id
            WHERE e.evaluated_at=(SELECT MAX(x.evaluated_at) FROM slo_evaluations x WHERE x.slo_id=e.slo_id)
            ORDER BY e.slo_id LIMIT ?""", (max(1, min(limit, 100)),)
        )
        for row in rows:
            row["evidence"] = json.loads(row.pop("evidence_json"))
        return rows
