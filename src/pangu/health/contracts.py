"""Typed health contracts shared by the service, API and tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


HealthStatus = Literal["HEALTHY", "DEGRADED", "UNHEALTHY"]


@dataclass(frozen=True)
class HealthComponent:
    """One independently scored and evidenced engineering health dimension."""

    name: str
    status: HealthStatus
    score: float
    message: str
    evidence: dict[str, Any]
    data_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["data_gaps"] = list(self.data_gaps)
        return payload
