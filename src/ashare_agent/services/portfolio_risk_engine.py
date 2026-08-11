from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

import pandas as pd

from ..investment_profile import InvestmentProfile


PORTFOLIO_RISK_ENGINE_VERSION = "portfolio-risk-v1.1.0"
STRESS_MODEL_VERSION = "historical-var-stress-v1.0.0"
STYLE_COLUMNS = {
    "value": "points_value",
    "quality": "points_quality",
    "growth": "points_growth",
    "momentum": "points_momentum",
    "trend": "points_trend",
    "low_risk": "points_low_risk",
    "liquidity": "points_liquidity",
}
CYCLICAL_INDUSTRY_MARKERS = (
    "有色", "钢铁", "煤炭", "化工", "建材", "机械", "汽车", "航运", "地产",
)


class PortfolioRiskEngine:
    """Assess security and portfolio risk without approving or creating orders."""

    def security_risk(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Score one stock from point-in-time volatility, drawdown and quality evidence."""
        volatility = self._number(row.get("volatility"), 0.50)
        max_drawdown = self._number(row.get("max_drawdown"), -0.50)
        momentum = self._number(row.get("momentum"), 0.0)
        trend = self._number(row.get("trend_strength"), 0.0)
        turnover = self._number(row.get("median_turnover_20d") or row.get("turnover"), 0.0)
        profit_growth = self._number(row.get("profit_growth"), 0.0)
        revenue_growth = self._number(row.get("revenue_growth"), 0.0)
        roe = self._number(row.get("roe"), 0.0)
        cash_quality = self._number(row.get("cash_quality"), 0.0)
        value_points = self._number(row.get("points_value"), 10.0)
        valuation_complete = bool(row.get("valuation_complete", False))
        capital_flow = self._optional_number(
            row.get("net_inflow_20d_ratio", row.get("capital_flow_20d_ratio"))
        )

        components = {
            "volatility": min(20.0, max(0.0, volatility) / 0.50 * 20.0),
            "drawdown": min(20.0, abs(min(0.0, max_drawdown)) / 0.50 * 20.0),
            "technical": (7.5 if momentum <= 0 else 0.0) + (7.5 if trend <= 0 else 0.0),
            "fundamental": (
                (5.0 if profit_growth < 0 else 0.0)
                + (4.0 if revenue_growth < 0 else 0.0)
                + (3.0 if roe < 5 else 0.0)
                + (3.0 if cash_quality < 50 else 0.0)
            ),
            "valuation": (
                min(10.0, max(0.0, (20.0 - value_points) / 20.0 * 10.0))
                if valuation_complete
                else 7.5
            ),
            "liquidity": min(10.0, max(0.0, (100_000_000.0 - turnover) / 100_000_000.0 * 10.0)),
            "capital_flow": (
                5.0
                if capital_flow is None
                else min(10.0, max(0.0, -capital_flow / 0.10 * 10.0))
            ),
        }
        score = min(100.0, sum(components.values()))
        level = "high" if score >= 70 else "medium" if score >= 40 else "low"
        return {
            "symbol": str(row.get("symbol") or ""),
            "risk_score": score,
            "risk_level": level,
            "components": components,
            "data_coverage": {
                "valuation": valuation_complete,
                "capital_flow_20d": capital_flow is not None,
            },
        }

    def assess_target_portfolio(
        self,
        ranked: pd.DataFrame,
        target_portfolio: Mapping[str, Any],
        profile: InvestmentProfile,
        *,
        valuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Calculate exposure and concentration while sourcing drawdown from valuation."""
        rows = ranked.set_index("symbol", drop=False) if not ranked.empty else pd.DataFrame()
        industry_exposure: dict[str, float] = {}
        style_values = {name: 0.0 for name in STYLE_COLUMNS}
        size_exposure = {"large_cap": 0.0, "mid_cap": 0.0, "small_cap": 0.0, "unknown": 0.0}
        cycle_exposure = {"cyclical": 0.0, "non_cyclical": 0.0, "unknown": 0.0}
        security_risks: list[dict[str, Any]] = []
        weights: list[float] = []
        stress_loss = 0.0
        portfolio_volatility_proxy = 0.0
        for position in target_portfolio.get("positions", []):
            symbol = str(position["symbol"])
            weight = float(position["target_weight"])
            weights.append(weight)
            row = rows.loc[symbol].to_dict() if symbol in rows.index else {"symbol": symbol}
            raw_industry = row.get("industry")
            industry = (
                "UNKNOWN"
                if raw_industry is None or pd.isna(raw_industry) or not str(raw_industry).strip()
                else str(raw_industry)
            )
            industry_exposure[industry] = industry_exposure.get(industry, 0.0) + weight
            risk = self.security_risk(row)
            risk["target_weight"] = weight
            security_risks.append(risk)
            for name, column in STYLE_COLUMNS.items():
                style_values[name] += weight * max(0.0, self._number(row.get(column), 0.0))
            market_cap = self._optional_number(row.get("market_cap"))
            if market_cap is None or market_cap <= 0:
                size_exposure["unknown"] += weight
            elif market_cap >= 100_000_000_000:
                size_exposure["large_cap"] += weight
            elif market_cap >= 30_000_000_000:
                size_exposure["mid_cap"] += weight
            else:
                size_exposure["small_cap"] += weight
            if industry == "UNKNOWN":
                cycle_exposure["unknown"] += weight
            elif any(marker in industry for marker in CYCLICAL_INDUSTRY_MARKERS):
                cycle_exposure["cyclical"] += weight
            else:
                cycle_exposure["non_cyclical"] += weight
            volatility = max(0.0, self._number(row.get("volatility"), 0.50))
            portfolio_volatility_proxy += weight * volatility
            historical_loss = abs(min(0.0, self._number(row.get("max_drawdown"), -0.50)))
            var_20d = 2.33 * volatility * math.sqrt(20.0 / 252.0)
            stress_loss += weight * min(1.0, max(historical_loss, var_20d))

        total_style = sum(style_values.values())
        style_exposure = {
            name: (value / total_style if total_style > 0 else 0.0)
            for name, value in style_values.items()
        }
        hhi = sum(weight * weight for weight in weights)
        top_weights = sorted(weights, reverse=True)
        concentration = {
            "hhi": hhi,
            "effective_positions": (1.0 / hhi if hhi > 0 else 0.0),
            "top1_weight": top_weights[0] if top_weights else 0.0,
            "top3_weight": sum(top_weights[:3]),
        }
        weighted_security_risk = sum(
            item["risk_score"] * item["target_weight"] for item in security_risks
        ) / max(sum(weights), 1e-12)
        largest_industry = max(industry_exposure.values(), default=0.0)
        concentration_surcharge = (
            max(0.0, hhi - 0.10) * 0.10
            + max(0.0, largest_industry - 0.25) * 0.10
        )
        predicted_max_drawdown = -min(1.0, stress_loss + concentration_surcharge)
        assessment = {
            "engine_version": PORTFOLIO_RISK_ENGINE_VERSION,
            "stress_model_version": STRESS_MODEL_VERSION,
            "profile_id": profile.profile_id,
            "source_run_id": target_portfolio.get("source_run_id"),
            "security_risks": security_risks,
            "weighted_security_risk": weighted_security_risk,
            "industry_exposure": industry_exposure,
            "style_exposure": style_exposure,
            "size_exposure": size_exposure,
            "cycle_exposure": cycle_exposure,
            "concentration": concentration,
            "portfolio_volatility_proxy": portfolio_volatility_proxy,
            "volatility_method": "目标权重加权个股年化波动率；未假设相关性，仅作保守暴露代理",
            "predicted_max_drawdown": predicted_max_drawdown,
            "predicted_drawdown_method": (
                "目标权重×max(120日历史回撤,20日99%正态VaR)+集中度附加；仅为压力代理"
            ),
            "max_drawdown": None,
            "current_drawdown": None,
            "drawdown_source": "ValuationService_required",
            "risk_flags": self._exposure_flags(
                industry_exposure,
                concentration,
                weighted_security_risk,
                predicted_max_drawdown,
                profile,
            ),
            "blocks_orders": False,
        }
        return self.with_valuation(assessment, valuation, profile) if valuation else assessment

    def with_valuation(
        self,
        assessment: Mapping[str, Any],
        valuation: Mapping[str, Any],
        profile: InvestmentProfile,
    ) -> dict[str, Any]:
        """Attach canonical drawdown fields without recalculating assets or performance."""
        required = {"service_version", "drawdown", "max_drawdown"}
        if not required.issubset(valuation):
            raise ValueError("Portfolio Risk Center必须读取ValuationService完整回撤合同")
        result = deepcopy(dict(assessment))
        result["max_drawdown"] = float(valuation["max_drawdown"])
        result["current_drawdown"] = float(valuation["drawdown"])
        result["drawdown_source"] = str(valuation["service_version"])
        flags = list(result.get("risk_flags", []))
        if abs(min(0.0, result["max_drawdown"])) >= profile.max_drawdown_tolerance:
            flags.append({
                "code": "MAX_DRAWDOWN_TOLERANCE_BREACHED",
                "severity": "high",
                "message": "组合最大回撤达到用户容忍上限",
            })
        result["risk_flags"] = flags
        return result

    @staticmethod
    def _exposure_flags(
        industry_exposure: Mapping[str, float],
        concentration: Mapping[str, float],
        weighted_security_risk: float,
        predicted_max_drawdown: float,
        profile: InvestmentProfile,
    ) -> list[dict[str, str]]:
        """Create deterministic explainable risk flags from portfolio exposures."""
        flags: list[dict[str, str]] = []
        industry_limit = {"conservative": 0.25, "balanced": 0.35, "aggressive": 0.45}[
            profile.risk_level
        ]
        for industry, exposure in industry_exposure.items():
            if exposure > industry_limit:
                flags.append({
                    "code": "INDUSTRY_CONCENTRATION",
                    "severity": "medium",
                    "message": f"{industry}行业暴露{exposure:.1%}超过{industry_limit:.1%}",
                })
        if concentration["top1_weight"] > profile.maximum_single_weight + 1e-9:
            flags.append({
                "code": "SINGLE_NAME_CONCENTRATION",
                "severity": "high",
                "message": "单股票目标仓位超过用户画像上限",
            })
        if concentration["hhi"] > 0.25:
            flags.append({
                "code": "PORTFOLIO_HHI_HIGH",
                "severity": "medium",
                "message": "组合集中度HHI偏高",
            })
        if weighted_security_risk >= 70:
            flags.append({
                "code": "WEIGHTED_SECURITY_RISK_HIGH",
                "severity": "high",
                "message": "持仓加权个股风险评分偏高",
            })
        if abs(min(0.0, predicted_max_drawdown)) >= profile.max_drawdown_tolerance:
            flags.append({
                "code": "PREDICTED_DRAWDOWN_TOLERANCE_BREACHED",
                "severity": "high",
                "message": "组合压力回撤代理达到用户容忍上限",
            })
        return flags

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        """Return a finite optional number without inventing unavailable evidence."""
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: Any, default: float) -> float:
        """Convert nullable model evidence into a finite deterministic number."""
        try:
            result = float(value)
            return result if math.isfinite(result) else default
        except (TypeError, ValueError):
            return default
