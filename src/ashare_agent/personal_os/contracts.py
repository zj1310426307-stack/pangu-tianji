from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


PERSONAL_OS_CONTRACT_VERSION = "personal-investment-os-v1.0.0"
PERSONAL_OS_SCHEMA_VERSION = "personal-os-schema-v1.0.0"
EVENT_TYPES = {"BUY", "SELL", "OBSERVE", "REVIEW", "LEARN"}
JOURNAL_TYPES = {"buy_reason", "sell_reason", "observation", "review", "lesson"}
KNOWLEDGE_CATEGORIES = {"company", "market", "personal_lesson"}
REPORT_TYPES = {"weekly_committee", "monthly_review", "personal_coach"}
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9._:-]{3,240}$")


def utc_now() -> str:
    """Return one timezone-aware timestamp for immutable audit records."""
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    """Serialize deterministic JSON so evidence identities can be replayed."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_hash(value: Any) -> str:
    """Create a canonical SHA-256 identity without including secrets."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(namespace: str, *parts: str) -> str:
    """Build a deterministic non-secret identifier for idempotent writes."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{namespace}-{digest}"


def validate_iso_date(value: str) -> str:
    """Require canonical ISO dates for report and event partitions."""
    parsed = date.fromisoformat(str(value))
    if parsed.isoformat() != str(value):
        raise ValueError("日期必须为YYYY-MM-DD")
    return parsed.isoformat()


def validate_evidence_ids(values: Sequence[str] | None) -> list[str]:
    """Normalize unique evidence references before any report is persisted."""
    items = [str(item).strip() for item in values or []]
    if any(not _EVIDENCE_ID.fullmatch(item) for item in items):
        raise ValueError("evidence_id格式无效")
    return list(dict.fromkeys(items))


def safety_contract() -> dict[str, bool]:
    """Return the immutable zero-authority contract shared by Personal OS DTOs."""
    return {
        "used_for_execution": False,
        "can_affect_execution": False,
        "can_trade": False,
        "can_create_orders": False,
        "can_modify_strategy": False,
        "can_modify_factor_weights": False,
        "can_modify_portfolio": False,
        "can_modify_risk": False,
        "can_approve_strategy": False,
        "can_launch_experiment": False,
    }


def evidence_ref(
    evidence_id: str,
    *,
    source_type: str,
    source_id: str,
    payload: Mapping[str, Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Create one self-verifying reference for a backend-owned evidence payload."""
    validate_evidence_ids([evidence_id])
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "source_id": source_id,
        "observed_at": observed_at,
        "payload_hash": content_hash(payload),
        "payload": dict(payload),
    }

