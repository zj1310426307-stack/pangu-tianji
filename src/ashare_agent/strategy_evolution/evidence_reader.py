from __future__ import annotations

from typing import Any, Mapping

from ..quant_ai.experiment_reader import QuantAIExperimentReader
from .contracts import EvolutionEvidenceError, FACTOR_NAMES, StrategyObservation


class StrategyEvolutionEvidenceReader:
    """Normalize hash-verified Quant Lab evidence into monitor-only observations."""

    reader_version = "strategy-evolution-evidence-reader-v1.0.0"

    def __init__(self, reader: QuantAIExperimentReader) -> None:
        self.reader = reader

    def read(self, review_id: str | None = None) -> StrategyObservation:
        """Read one sealed review; hash failures from upstream remain fatal."""
        bundle = self.reader.read(review_id) if review_id else self.reader.latest()
        if not bundle.strategy_id or not bundle.strategy_version or not bundle.items:
            raise EvolutionEvidenceError("没有可用于策略进化监控的正式准入证据")
        payloads = {item.source_type: dict(item.payload) for item in bundle.items}
        validation = payloads.get("strategy_validation_report")
        if not validation:
            raise EvolutionEvidenceError("缺少策略准入委员会报告")
        if (
            validation.get("strategy_id") != bundle.strategy_id
            or validation.get("strategy_version") != bundle.strategy_version
        ):
            raise EvolutionEvidenceError("准入报告与证据包策略身份不一致")
        review_identity = str(validation.get("review_id") or review_id or "")
        if not review_identity:
            raise EvolutionEvidenceError("准入报告缺少review_id")
        factor_report = payloads.get("factor_research_report") or {}
        robust_report = payloads.get("strategy_robustness_report") or {}
        market = payloads.get("registered_market_regime_evidence") or {}
        sections = validation.get("sections") or {}
        robust_sections = robust_report.get("sections") or {}
        baseline = robust_sections.get("03_baseline") or {}
        oos = (sections.get("04_robustness_and_oos") or {}).get("out_of_sample") or {}
        paper = (sections.get("04_robustness_and_oos") or {}).get("paper_execution") or {}
        paper_performance = self._check_value(paper, "PAPER_PERFORMANCE_RECORDED") or {}
        performance = {
            "annual_return": self._number(baseline.get("annual_return")),
            "total_return": self._number(paper_performance.get("total_return")),
            "sharpe": self._number(
                baseline.get("sharpe")
                if baseline.get("sharpe") is not None
                else self._check_value(oos, "OOS_SHARPE")
            ),
            "win_rate": self._number(
                baseline.get("win_rate")
                if baseline.get("win_rate") is not None
                else baseline.get("hit_rate")
            ),
        }
        risk = {
            "max_drawdown": self._number(
                baseline.get("max_drawdown")
                if baseline.get("max_drawdown") is not None
                else self._check_value(oos, "OOS_DRAWDOWN")
            ),
            "volatility": self._number(
                baseline.get("annual_volatility")
                if baseline.get("annual_volatility") is not None
                else baseline.get("volatility")
            ),
        }
        execution = {
            "turnover": self._number(baseline.get("turnover")),
            "cost_ratio": self._number(
                baseline.get("cost_ratio")
                if baseline.get("cost_ratio") is not None
                else baseline.get("fee_ratio")
            ),
            "execution_deviation": self._number(
                self._check_value(paper, "EXECUTION_DEVIATION")
            ),
        }
        factor_metrics = self._factor_metrics(factor_report)
        robust_regime = market.get("robustness_regime") or robust_sections.get(
            "07_market_regime"
        ) or {}
        factor_regime = market.get("factor_regime") or (
            (factor_report.get("sections") or {}).get("08_market_regime") or {}
        )
        environment = {
            "availability": robust_regime.get("availability")
            or factor_regime.get("availability") or "UNAVAILABLE",
            "regime_source": robust_regime.get("regime_source")
            or factor_regime.get("regime_source"),
            "stability_score": self._number(robust_regime.get("stability_score")),
            "market_label_inferred": False,
        }
        observed_at = max(
            (str(item.observed_at) for item in bundle.items if item.observed_at),
            default=bundle.generated_at,
        )
        gaps = list(bundle.data_gaps)
        if not factor_report:
            gaps.append("factor_research_report_unavailable")
        if not robust_report:
            gaps.append("strategy_robustness_report_unavailable")
        return StrategyObservation(
            strategy_id=str(bundle.strategy_id),
            strategy_version=str(bundle.strategy_version),
            review_id=review_identity,
            observed_at=observed_at,
            dataset_label=str(bundle.dataset_label or validation.get("dataset_label") or ""),
            performance=performance,
            risk=risk,
            factors=factor_metrics,
            execution=execution,
            environment=environment,
            evidence_ids=tuple(item.evidence_id for item in bundle.items),
            evidence_hash=bundle.evidence_hash,
            data_gaps=tuple(dict.fromkeys(gaps)),
        )

    @classmethod
    def _factor_metrics(cls, report: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
        """Read the seven named factors from formal Factor Lab report sections."""
        sections = report.get("sections") or {}
        ic_section = sections.get("03_ic_analysis") or {}
        contribution = (sections.get("07_ablation") or {}).get("contributions") or {}
        result: dict[str, dict[str, float | None]] = {}
        for name in FACTOR_NAMES:
            horizons = ic_section.get(name) or {}
            selected = horizons.get("20") or horizons.get(20)
            if not isinstance(selected, Mapping):
                selected = next(
                    (value for value in horizons.values() if isinstance(value, Mapping)), {}
                )
            factor_contribution = contribution.get(name)
            if isinstance(factor_contribution, Mapping):
                factor_contribution = factor_contribution.get("contribution")
            result[name] = {
                "mean": cls._number(selected.get("factor_mean")),
                "std": cls._number(selected.get("factor_std")),
                "ic": cls._number(
                    selected.get("rank_ic_mean")
                    if selected.get("rank_ic_mean") is not None
                    else selected.get("ic_mean")
                    if selected.get("ic_mean") is not None
                    else selected.get("ic")
                ),
                "icir": cls._number(selected.get("icir")),
                "hit_rate": cls._number(selected.get("ic_hit_rate")),
                "contribution": cls._number(factor_contribution),
            }
        return result

    @staticmethod
    def _check_value(section: Mapping[str, Any], code: str) -> Any:
        """Return an exact gate check value; never search arbitrary report text."""
        for item in section.get("checks") or ():
            if isinstance(item, Mapping) and item.get("code") == code:
                return item.get("actual")
        return None

    @staticmethod
    def _number(value: Any) -> float | None:
        """Normalize finite numbers and leave missing metrics unavailable."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if result == result and abs(result) != float("inf") else None

