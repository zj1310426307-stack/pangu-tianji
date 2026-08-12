"""Strong, non-executable contracts for Pangu runtime telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping


OBSERVABILITY_SERVICE_VERSION = "pangu-observability-v1.0.0"
OBSERVABILITY_SCHEMA_VERSION = "pangu-observability-db-v1.0.0"
_NAME = re.compile(r"^[a-z][a-z0-9_.]{2,95}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
ALLOWED_LABELS = frozenset(
    {
        "operation_id", "method", "status_class", "component", "job_type",
        "status", "model", "prompt_version", "database", "source",
    }
)
ALLOWED_UNITS = frozenset({"count", "ms", "seconds", "bytes", "percent", "score", "ratio", "timestamp"})


class MetricType(StrEnum):
    COUNTER = "COUNTER"
    GAUGE = "GAUGE"
    HISTOGRAM = "HISTOGRAM"
    DURATION = "DURATION"
    STATUS = "STATUS"


class TelemetryStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"


class JobStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class SLOStatus(StrEnum):
    HEALTHY = "HEALTHY"
    AT_RISK = "AT_RISK"
    BREACHED = "BREACHED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


def canonical_hash(payload: Any) -> str:
    """Hash one JSON-safe evidence object deterministically."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_identifier(value: str, field_name: str) -> str:
    """Reject unsafe or unbounded correlation identifiers."""
    normalized = str(value or "").strip()
    if not _ID.fullmatch(normalized):
        raise ValueError(f"{field_name} 格式无效")
    return normalized


def validate_labels(labels: Mapping[str, str] | None) -> dict[str, str]:
    """Enforce a small label vocabulary so metric cardinality stays bounded."""
    result: dict[str, str] = {}
    for key, value in dict(labels or {}).items():
        if key not in ALLOWED_LABELS:
            raise ValueError(f"指标标签不在白名单：{key}")
        normalized = str(value)
        if len(normalized) > 64 or any(secret in normalized.lower() for secret in ("sk-", "bearer ", "authorization")):
            raise ValueError(f"指标标签值不安全：{key}")
        result[key] = normalized
    return result


@dataclass(frozen=True)
class TelemetryContext:
    """Correlation identifiers propagated through API, CLI and explicit jobs."""

    trace_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    research_run_id: str | None = None
    experiment_id: str | None = None
    report_id: str | None = None
    backup_id: str | None = None
    operation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return only populated, bounded identifiers."""
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class MetricSample:
    """One immutable metric observation with safe low-cardinality labels."""

    metric_name: str
    metric_type: MetricType
    timestamp: str
    value: float
    unit: str
    labels: Mapping[str, str] = field(default_factory=dict)
    source: str = "pangu"
    trace_id: str | None = None
    evidence_hash: str = ""

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.metric_name):
            raise ValueError("metric_name 必须为稳定的点分小写名称")
        if self.unit not in ALLOWED_UNITS:
            raise ValueError(f"不支持的指标单位：{self.unit}")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("指标值必须为数值")
        safe = validate_labels(self.labels)
        object.__setattr__(self, "labels", safe)
        if self.trace_id is not None:
            validate_identifier(self.trace_id, "trace_id")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metric_type"] = self.metric_type.value
        payload["labels"] = dict(self.labels)
        if not payload["evidence_hash"]:
            payload["evidence_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "evidence_hash"})
        return payload


TERMINAL_JOBS = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED, JobStatus.STALE})
JOB_TRANSITIONS = {
    JobStatus.CREATED: frozenset({JobStatus.RUNNING, JobStatus.BLOCKED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED, JobStatus.STALE}),
}

INCIDENT_TRANSITIONS = {
    IncidentStatus.OPEN: frozenset({IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATED, IncidentStatus.RESOLVED}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.MITIGATED, IncidentStatus.RESOLVED}),
    IncidentStatus.MITIGATED: frozenset({IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED}),
    IncidentStatus.RESOLVED: frozenset({IncidentStatus.INVESTIGATING, IncidentStatus.CLOSED}),
    IncidentStatus.CLOSED: frozenset(),
}
