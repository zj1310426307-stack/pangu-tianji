from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

import pandas as pd

from ..investment_profile import InvestmentProfile
from ..research_pipeline import ResearchPipelineResult


PORTFOLIO_SERVICE_VERSION = "portfolio-service-v1.1.0"
POSITIONING_MODEL_VERSION = "score-risk-sizing-v1.0.0"
STYLE_POINT_COLUMNS = {
    "value": ("points_value",),
    "value_growth": ("points_value", "points_growth"),
    "quality": ("points_quality",),
    "growth": ("points_growth",),
    "momentum": ("points_momentum",),
    "low_volatility": ("points_low_risk",),
}


class PortfolioServiceError(RuntimeError):
    """Reject a target portfolio when research and evidence contracts disagree."""


@dataclass(frozen=True)
class TargetPosition:
    """Describe one account-independent target position without creating an order."""

    symbol: str
    name: str
    industry: str | None
    research_rank: int
    research_score: float
    security_risk_score: float | None
    base_weight: float
    score_coefficient: float
    risk_coefficient: float
    style_coefficient: float
    model_weight: float
    target_weight: float
    target_value: float
    reference_price: float
    round_lot_quantity: int


class PortfolioService:
    """Generate profile-aware targets and rebalance intent from research evidence."""

    def build_target_portfolio(
        self,
        result: ResearchPipelineResult,
        evidence: Mapping[str, Any],
        profile: InvestmentProfile,
        security_risks: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Build a score- and risk-sized portfolio without submitting orders."""
        self._validate_evidence(result, evidence)
        ranked = result.ranked.copy()
        selected = ranked[ranked["symbol"].astype(str).isin(result.targets)].copy()
        if selected.empty:
            raise PortfolioServiceError("ResearchPipeline没有可构建目标组合的标的")
        upstream_exposure = sum(float(result.target_weights.get(symbol, 0.0)) for symbol in result.targets)
        exposure_cap = min(profile.maximum_exposure, upstream_exposure)
        single_cap = min(
            profile.maximum_single_weight,
            max(float(value) for value in result.target_weights.values()),
        )
        risk_by_symbol = {
            str(item.get("symbol")): min(100.0, max(0.0, float(item.get("risk_score", 50.0))))
            for item in security_risks
            if item.get("symbol")
        }
        allocations = self._dynamic_allocations(
            selected,
            result,
            profile,
            risk_by_symbol,
        )
        weights = self._cap_without_forcing_exposure(
            {symbol: item["raw_weight"] for symbol, item in allocations.items()},
            exposure_cap,
            single_cap,
        )

        positions: list[TargetPosition] = []
        selected_by_symbol = selected.set_index("symbol", drop=False)
        for symbol, model_weight in weights.items():
            row = selected_by_symbol.loc[symbol]
            price = float(row["last_price"])
            target_value = profile.capital * model_weight
            quantity = math.floor(target_value / price / 100) * 100 if price > 0 else 0
            if quantity <= 0:
                continue
            round_lot_value = quantity * price
            target_weight = round_lot_value / profile.capital
            coefficients = allocations[str(symbol)]
            raw_industry = row.get("industry")
            industry = (
                None
                if raw_industry is None or pd.isna(raw_industry) or not str(raw_industry).strip()
                else str(raw_industry)
            )
            positions.append(TargetPosition(
                symbol=str(symbol),
                name=str(row.get("name") or symbol),
                industry=industry,
                research_rank=int(row["rank"]),
                research_score=float(row["score"]),
                security_risk_score=float(coefficients["risk_score"]),
                base_weight=float(coefficients["base_weight"]),
                score_coefficient=float(coefficients["score_coefficient"]),
                risk_coefficient=float(coefficients["risk_coefficient"]),
                style_coefficient=float(coefficients["style_coefficient"]),
                model_weight=float(model_weight),
                target_weight=float(target_weight),
                target_value=float(round_lot_value),
                reference_price=price,
                round_lot_quantity=int(quantity),
            ))
        realized_exposure = sum(item.target_weight for item in positions)
        return {
            "service_version": PORTFOLIO_SERVICE_VERSION,
            "positioning_model_version": POSITIONING_MODEL_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_run_id": str(evidence["run_id"]),
            "data_version": str(evidence["data_version"]),
            "strategy_version": result.strategy_version,
            "factor_version": result.factor_model_version,
            "factor_contract_hash": result.factor_contract_hash,
            "profile": profile.to_dict(),
            "target_exposure": realized_exposure,
            "model_exposure_before_lot_rounding": sum(weights.values()),
            "upstream_exposure": upstream_exposure,
            "exposure_cap": exposure_cap,
            "cash_reserve_weight": max(0.0, 1.0 - realized_exposure),
            "single_name_cap": single_cap,
            "positions": [asdict(item) for item in positions],
            "execution_authorized": False,
            "message": "目标组合仅用于投资辅助与模拟计划，不生成证券订单",
        }

    def rebalance_actions(
        self,
        target_portfolio: Mapping[str, Any],
        current_positions: Iterable[Mapping[str, Any]],
        profile: InvestmentProfile,
    ) -> list[dict[str, Any]]:
        """Compare service-valued current weights with targets and return intent only."""
        targets = {
            str(item["symbol"]): float(item["target_weight"])
            for item in target_portfolio.get("positions", [])
        }
        current = {
            str(item["symbol"]): float(item.get("weight") or 0.0)
            for item in current_positions
        }
        actions: list[dict[str, Any]] = []
        for symbol in sorted(set(targets) | set(current)):
            target_weight = targets.get(symbol, 0.0)
            current_weight = current.get(symbol, 0.0)
            drift = target_weight - current_weight
            if abs(drift) < profile.rebalance_threshold:
                action = "HOLD"
            elif drift > 0:
                action = "INCREASE"
            elif target_weight == 0:
                action = "EXIT"
            else:
                action = "REDUCE"
            actions.append({
                "symbol": symbol,
                "action": action,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "weight_drift": drift,
                "threshold": profile.rebalance_threshold,
                "creates_order": False,
            })
        return actions

    @staticmethod
    def _validate_evidence(
        result: ResearchPipelineResult,
        evidence: Mapping[str, Any],
    ) -> None:
        """Require Data Center evidence to match the supplied Pipeline result."""
        required = {"run_id", "data_version", "strategy_version", "factor_version", "factor_contract_hash"}
        missing = sorted(required - set(evidence))
        if missing:
            raise PortfolioServiceError(f"Data Center证据缺少字段：{missing}")
        if evidence["strategy_version"] != result.strategy_version:
            raise PortfolioServiceError("Data Center strategy_version与研究结果不一致")
        if evidence["factor_version"] != result.factor_model_version:
            raise PortfolioServiceError("Data Center factor_version与研究结果不一致")
        if evidence["factor_contract_hash"] != result.factor_contract_hash:
            raise PortfolioServiceError("Data Center factor_contract_hash与研究结果不一致")

    @staticmethod
    def _dynamic_allocations(
        selected: pd.DataFrame,
        result: ResearchPipelineResult,
        profile: InvestmentProfile,
        risk_by_symbol: Mapping[str, float],
    ) -> dict[str, dict[str, float]]:
        """Apply the documented base × score × risk × style sizing formula."""
        base = {
            str(symbol): float(result.target_weights.get(str(symbol), 0.0))
            for symbol in selected["symbol"].astype(str)
        }
        selected_by_symbol = selected.set_index("symbol", drop=False)
        style_columns = [
            column
            for column in STYLE_POINT_COLUMNS.get(profile.investment_style, ())
            if column in selected
        ]
        style_rank = pd.Series(0.5, index=selected_by_symbol.index, dtype=float)
        if style_columns:
            style_points = selected_by_symbol[style_columns].astype(float).mean(axis=1)
            style_rank = style_points.rank(method="average", pct=True).fillna(0.5)

        allocations: dict[str, dict[str, float]] = {}
        for symbol, base_weight in base.items():
            score = min(100.0, max(0.0, float(selected_by_symbol.loc[symbol, "score"])))
            risk_score = risk_by_symbol.get(symbol, 50.0)
            score_coefficient = min(1.25, max(0.50, score / 80.0))
            risk_coefficient = min(0.95, max(0.25, 1.0 - risk_score / 100.0))
            style_coefficient = 0.8 + 0.4 * float(style_rank.loc[symbol])
            allocations[symbol] = {
                "base_weight": max(0.0, base_weight),
                "score_coefficient": score_coefficient,
                "risk_coefficient": risk_coefficient,
                "style_coefficient": style_coefficient,
                "risk_score": risk_score,
                "raw_weight": max(
                    0.0,
                    base_weight * score_coefficient * risk_coefficient * style_coefficient,
                ),
            }
        return allocations

    @staticmethod
    def _cap_without_forcing_exposure(
        raw: Mapping[str, float],
        exposure_cap: float,
        single_cap: float,
    ) -> dict[str, float]:
        """Cap risk-sized weights without scaling a defensive portfolio back to full risk."""
        result = {
            symbol: min(single_cap, max(0.0, float(value)))
            for symbol, value in raw.items()
            if value > 0
        }
        total = sum(result.values())
        if total > exposure_cap > 0:
            scale = exposure_cap / total
            result = {symbol: value * scale for symbol, value in result.items()}
        return result
