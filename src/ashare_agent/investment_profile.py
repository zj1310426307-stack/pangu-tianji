from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Mapping


INVESTMENT_PROFILE_VERSION = "investment-profile-v1.0.0"
RISK_LEVELS = {"conservative", "balanced", "aggressive"}
INVESTMENT_HORIZONS = {"short", "medium", "long"}
INVESTMENT_STYLES = {
    "balanced",
    "value",
    "value_growth",
    "quality",
    "growth",
    "momentum",
    "low_volatility",
}
RISK_LEVEL_ALIASES = {
    "low": "conservative",
    "medium": "balanced",
    "high": "aggressive",
}
INVESTMENT_HORIZON_ALIASES = {
    "1_year": "short",
    "3_year": "long",
    "short_term": "short",
    "medium_term": "medium",
    "long_term": "long",
}


@dataclass(frozen=True)
class InvestmentProfile:
    """Represent the user-owned constraints consumed by portfolio construction."""

    capital: float
    risk_level: str
    investment_horizon: str
    max_drawdown_tolerance: float
    investment_style: str
    version: str = INVESTMENT_PROFILE_VERSION

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None,
        *,
        default_capital: float = 100_000.0,
    ) -> "InvestmentProfile":
        """Build and validate a profile without relying on frontend defaults."""
        values = values or {}
        risk_level = str(values.get("risk_level", values.get("risk", "balanced")))
        risk_level = RISK_LEVEL_ALIASES.get(risk_level, risk_level)
        horizon = str(
            values.get("investment_horizon", values.get("investment_period", "medium"))
        )
        horizon = INVESTMENT_HORIZON_ALIASES.get(horizon, horizon)
        drawdown = float(
            values.get("max_drawdown_tolerance", values.get("max_drawdown", 0.10))
        )
        if 1 < drawdown <= 30:
            drawdown /= 100.0
        profile = cls(
            capital=float(values.get("capital", default_capital)),
            risk_level=risk_level,
            investment_horizon=horizon,
            max_drawdown_tolerance=drawdown,
            investment_style=str(values.get("investment_style", "balanced")),
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        """Reject incomplete or unsafe profile values before portfolio use."""
        if not math.isfinite(self.capital) or self.capital <= 0:
            raise ValueError("Investment Profile资金规模必须为正数")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"Investment Profile风险等级仅允许{sorted(RISK_LEVELS)}")
        if self.investment_horizon not in INVESTMENT_HORIZONS:
            raise ValueError(
                f"Investment Profile投资周期仅允许{sorted(INVESTMENT_HORIZONS)}"
            )
        if not 0.03 <= self.max_drawdown_tolerance <= 0.30:
            raise ValueError("Investment Profile最大回撤容忍必须在3%到30%之间")
        if self.investment_style not in INVESTMENT_STYLES:
            raise ValueError(f"Investment Profile投资风格仅允许{sorted(INVESTMENT_STYLES)}")

    @property
    def profile_id(self) -> str:
        """Return a stable non-secret identity for the current profile contract."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "profile-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    @property
    def maximum_exposure(self) -> float:
        """Translate risk level and drawdown budget into a deterministic exposure cap."""
        risk_cap = {"conservative": 0.40, "balanced": 0.60, "aggressive": 0.75}[
            self.risk_level
        ]
        drawdown_scale = min(1.0, self.max_drawdown_tolerance / 0.10)
        return risk_cap * drawdown_scale

    @property
    def maximum_single_weight(self) -> float:
        """Return the user-level single-name concentration limit."""
        return {"conservative": 0.08, "balanced": 0.15, "aggressive": 0.20}[
            self.risk_level
        ]

    @property
    def rebalance_threshold(self) -> float:
        """Use a wider drift band for longer investment horizons."""
        return {"short": 0.03, "medium": 0.05, "long": 0.08}[
            self.investment_horizon
        ]

    def to_dict(self) -> dict[str, Any]:
        """Expose a JSON-safe profile snapshot for research evidence."""
        return {
            **asdict(self),
            "profile_id": self.profile_id,
            "maximum_exposure": self.maximum_exposure,
            "maximum_single_weight": self.maximum_single_weight,
            "rebalance_threshold": self.rebalance_threshold,
        }
