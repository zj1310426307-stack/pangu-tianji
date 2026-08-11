from __future__ import annotations

import math
from typing import Any, Mapping

from .contracts import safety_contract
from .store import PersonalOSStore


RISK_LEVELS = {"conservative", "balanced", "aggressive"}
HOLDING_PERIODS = {"short", "medium", "long"}
INVESTMENT_STYLES = {
    "balanced", "value", "value_growth", "quality", "growth", "momentum",
    "low_volatility",
}


class InvestorDigitalTwin:
    """Maintain a descriptive investor profile outside production constraints."""

    def __init__(self, store: PersonalOSStore) -> None:
        self.store = store

    def get(self, production_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return the persisted twin or a read-only seed from InvestmentProfile."""
        saved = self.store.profile()
        if saved:
            return {**saved, "persisted": True, "purpose": self._purpose()}
        source = dict(production_profile or {})
        return {
            "profile_id": "investor-digital-twin",
            "revision": 0,
            "capital": float(source.get("capital") or 100_000.0),
            "risk_level": str(source.get("risk_level") or "balanced"),
            "holding_period": str(source.get("investment_horizon") or "medium"),
            "investment_style": str(source.get("investment_style") or "balanced"),
            "max_drawdown": float(source.get("max_drawdown_tolerance") or 0.10),
            "behavior": [],
            "source_profile_id": source.get("profile_id"),
            "created_time": None,
            "updated_time": None,
            "persisted": False,
            "purpose": self._purpose(),
            **safety_contract(),
        }

    def update(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and persist the descriptive twin without changing YAML/config."""
        capital = float(values["capital"])
        max_drawdown = float(values["max_drawdown"])
        risk_level = str(values["risk_level"])
        holding_period = str(values["holding_period"])
        style = str(values["investment_style"])
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("资金规模必须为正数")
        if risk_level not in RISK_LEVELS:
            raise ValueError("风险等级无效")
        if holding_period not in HOLDING_PERIODS:
            raise ValueError("持有周期无效")
        if style not in INVESTMENT_STYLES:
            raise ValueError("投资风格无效")
        if not 0.03 <= max_drawdown <= 0.30:
            raise ValueError("最大回撤容忍度必须在3%至30%之间")
        saved = self.store.save_profile({
            "capital": capital,
            "risk_level": risk_level,
            "holding_period": holding_period,
            "investment_style": style,
            "max_drawdown": max_drawdown,
            "behavior": list(values.get("behavior") or []),
            "source_profile_id": values.get("source_profile_id"),
        })
        return {**saved, "persisted": True, "purpose": self._purpose()}

    @staticmethod
    def _purpose() -> str:
        return "仅用于个性化解释、纪律复盘与长期学习，不覆盖生产投资画像、策略或风控"

