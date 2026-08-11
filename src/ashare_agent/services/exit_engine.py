from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ..investment_profile import InvestmentProfile


EXIT_ENGINE_VERSION = "exit-engine-v1.1.0"


class ExitEngine:
    """Generate explainable exit intent without submitting or scheduling orders."""

    def evaluate(
        self,
        positions: Iterable[Mapping[str, Any]],
        candidates: Iterable[Mapping[str, Any]],
        security_risks: Iterable[Mapping[str, Any]],
        profile: InvestmentProfile,
        portfolio_risk: Mapping[str, Any] | None = None,
        *,
        hold_rank: int = 20,
    ) -> dict[str, Any]:
        """Evaluate score, fundamentals, risk and technical damage for each holding."""
        candidate_map = {str(item["symbol"]): dict(item) for item in candidates}
        risk_map = {str(item["symbol"]): dict(item) for item in security_risks}
        drawdown_breached = any(
            item.get("code") == "MAX_DRAWDOWN_TOLERANCE_BREACHED"
            for item in (portfolio_risk or {}).get("risk_flags", [])
        )
        signals: list[dict[str, Any]] = []
        for raw_position in positions:
            position = dict(raw_position)
            symbol = str(position["symbol"])
            candidate = candidate_map.get(symbol)
            reasons: list[dict[str, str]] = []
            severity = 0
            if candidate is None:
                reasons.append({"type": "SCORE_DECLINE", "message": "持仓已跌出当前候选榜"})
                severity = max(severity, 3)
            else:
                rank = int(candidate.get("rank") or 10_000)
                score = self._number(candidate.get("score"), 0.0)
                entry_score = self._number(position.get("entry_score"), score)
                score_decline = entry_score - score
                if rank > hold_rank or score_decline >= 25:
                    reasons.append({"type": "SCORE_DECLINE", "message": "评分或排名跌破退出阈值"})
                    severity = max(severity, 3)
                elif score_decline >= 15:
                    reasons.append({"type": "SCORE_DECLINE", "message": "评分下降进入观察区"})
                    severity = max(severity, 1)
                signals_data = candidate.get("signals") or candidate
                fundamental_bad = (
                    self._number(signals_data.get("profit_growth"), 0.0) < 0
                    and self._number(signals_data.get("revenue_growth"), 0.0) < 0
                )
                prior_profit = position.get("entry_profit_growth")
                if prior_profit is not None and (
                    self._number(prior_profit, 0.0)
                    - self._number(signals_data.get("profit_growth"), 0.0)
                    >= 20
                ):
                    fundamental_bad = True
                if fundamental_bad:
                    reasons.append({"type": "FUNDAMENTAL_DETERIORATION", "message": "营收与利润增长或相对入场时明显恶化"})
                    severity = max(severity, 3 if (
                        self._number(signals_data.get("profit_growth"), 0.0) < 0
                        and self._number(signals_data.get("revenue_growth"), 0.0) < 0
                    ) else 2)
                technical_damage = (
                    self._number(signals_data.get("momentum"), 0.0) <= 0
                    and self._number(signals_data.get("trend_strength"), 0.0) <= 0
                )
                price = self._number(position.get("last_price"), 0.0)
                ma20 = self._number(signals_data.get("ma20"), 0.0)
                ma60 = self._number(signals_data.get("ma60"), 0.0)
                if price > 0 and ma60 > 0 and price < ma60:
                    reasons.append({"type": "TECHNICAL_BREAKDOWN", "message": "价格跌破MA60，长期技术结构破坏"})
                    severity = max(severity, 3)
                elif technical_damage or (price > 0 and ma20 > 0 and price < ma20):
                    reasons.append({"type": "TECHNICAL_BREAKDOWN", "message": "动量与趋势转弱或价格跌破MA20"})
                    severity = max(severity, 2)
                capital_flow = self._optional_number(
                    signals_data.get(
                        "net_inflow_20d_ratio",
                        signals_data.get("capital_flow_20d_ratio"),
                    )
                )
                if capital_flow is not None and capital_flow <= -0.10:
                    reasons.append({"type": "CAPITAL_FLOW_DETERIORATION", "message": "20日资金流出比例达到退出阈值"})
                    severity = max(severity, 3)
                elif capital_flow is not None and capital_flow <= -0.05:
                    reasons.append({"type": "CAPITAL_FLOW_DETERIORATION", "message": "20日资金流持续恶化"})
                    severity = max(severity, 2)

            risk_score = self._number(risk_map.get(symbol, {}).get("risk_score"), 0.0)
            if risk_score >= 70 or drawdown_breached:
                reasons.append({"type": "RISK_TRIGGER", "message": "个股风险或组合回撤触发退出规则"})
                severity = max(severity, 3)
            elif risk_score >= 50:
                reasons.append({"type": "RISK_TRIGGER", "message": "个股风险评分升至中高区间"})
                severity = max(severity, 2)

            action = (
                "EXIT" if severity >= 3 else
                "REDUCE" if severity == 2 else
                "WATCH" if severity == 1 else
                "HOLD"
            )
            available = int(position.get("available_quantity") or 0)
            if action in {"EXIT", "REDUCE"} and available <= 0:
                action = "DEFER_T1"
            signals.append({
                "symbol": symbol,
                "action": action,
                "reasons": reasons,
                "available_quantity": available,
                "profile_id": profile.profile_id,
                "creates_order": False,
            })
        return {
            "engine_version": EXIT_ENGINE_VERSION,
            "profile_id": profile.profile_id,
            "signals": signals,
            "creates_orders": False,
            "supported_triggers": [
                "SCORE_DECLINE",
                "FUNDAMENTAL_DETERIORATION",
                "RISK_TRIGGER",
                "TECHNICAL_BREAKDOWN",
                "CAPITAL_FLOW_DETERIORATION",
            ],
        }

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        """Preserve missing capital-flow evidence instead of inventing a signal."""
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: Any, default: float) -> float:
        """Return a finite float while preserving fail-safe caller defaults."""
        try:
            result = float(value)
            return result if math.isfinite(result) else default
        except (TypeError, ValueError):
            return default
