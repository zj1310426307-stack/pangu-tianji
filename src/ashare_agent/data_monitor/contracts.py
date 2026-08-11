from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping


DATA_INTELLIGENCE_VERSION = "data-intelligence-platform-v1.0.0"
DATA_INTELLIGENCE_SCHEMA_VERSION = "data-intelligence-schema-v1.0.0"
DATA_HEALTH_WEIGHTS = {
    "completeness": 25,
    "freshness": 20,
    "coverage": 20,
    "anomaly": 20,
    "consistency": 15,
}
INCIDENT_LEVELS = {"WARNING", "ERROR", "BLOCKED"}
HEALTH_STATES = {"NORMAL", "WARNING", "ERROR", "BLOCKED", "NOT_EVALUATED"}
REQUIRED_ASSETS = {
    "raw_security_universe",
    "raw_market_snapshot",
    "raw_price_history",
    "raw_fundamentals",
    "security_master",
    "market_data_pit",
    "financial_point_in_time",
    "feature_store",
    "factor_store",
    "final_ranking",
    "portfolio_weights",
}


class DataIntelligenceError(RuntimeError):
    """Reject invalid monitoring state without changing upstream evidence."""


class DataQualityGateError(DataIntelligenceError):
    """Stop a formal publication when deterministic data checks are not healthy."""

    def __init__(self, assessment: Mapping[str, Any]) -> None:
        self.assessment = dict(assessment)
        state = self.assessment.get("status", "BLOCKED")
        score = self.assessment.get("score", 0)
        issues = self.assessment.get("issues") or []
        summary = str(issues[0].get("message")) if issues else "数据健康门禁未通过"
        super().__init__(f"DATA_{state}（{score}/100）：{summary}")


def utc_now() -> str:
    """Return one timezone-aware timestamp for audit records."""
    return datetime.now(timezone.utc).isoformat()


def clamp_score(value: Any) -> float:
    """Normalize a component score to a finite number in the 0..100 range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(max(0.0, min(100.0, number)), 2)


def canonical_json(value: Any) -> str:
    """Serialize monitoring evidence deterministically for hashes and identities."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    """Return a stable SHA-256 identifier for JSON-compatible evidence."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    """Build an idempotent public identifier without exposing source content."""
    digest = hashlib.sha256("|".join(str(item) for item in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


def issue(
    category: str,
    code: str,
    level: str,
    message: str,
    *,
    blocking: bool = False,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one bounded, deterministic data-quality issue."""
    normalized_level = str(level).upper()
    if normalized_level not in INCIDENT_LEVELS:
        raise DataIntelligenceError(f"无效数据事件等级：{level}")
    return {
        "category": str(category),
        "code": str(code),
        "level": normalized_level,
        "message": str(message)[:800],
        "blocking": bool(blocking),
        "details": dict(details or {}),
    }


def component(
    name: str,
    score: Any,
    checks: Mapping[str, Any],
    issues: list[dict[str, Any]],
    *,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Return one fixed-weight score component and its exact evidence."""
    normalized = clamp_score(score)
    weight = int(DATA_HEALTH_WEIGHTS[name])
    state = "BLOCKED" if any(item.get("blocking") for item in issues) else (
        "ERROR" if normalized < 70 else "WARNING" if normalized < 85 else "NORMAL"
    )
    return {
        "name": name,
        "weight": weight,
        "score": normalized,
        "contribution": round(normalized * weight / 100.0, 2),
        "status": state,
        "checks": dict(checks),
        "issues": list(issues),
        "notes": list(notes or []),
        "blocking": any(item.get("blocking") for item in issues),
    }


def safety_contract() -> dict[str, bool]:
    """Expose immutable zero-capability flags for the data intelligence layer."""
    return {
        "can_trade": False,
        "can_create_orders": False,
        "can_modify_historical_data": False,
        "can_modify_strategy": False,
        "can_modify_factor_weights": False,
        "can_bypass_gate": False,
    }
