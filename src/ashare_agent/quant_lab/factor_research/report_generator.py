from __future__ import annotations

from typing import Any

from ..contracts import ExperimentSpec, utc_now
from .contracts import FACTOR_NAMES, FactorResearchConfig


class FactorReportGenerator:
    """Generate a ten-section evidence report and a non-executing future AI contract."""

    generator_version = "factor-report-generator-v1.0.0"

    def generate(
        self,
        *,
        spec: ExperimentSpec,
        run_id: str,
        config: FactorResearchConfig,
        panel_manifest: dict[str, Any],
        metrics: dict[str, Any],
        ic: dict[str, Any],
        quantiles: dict[str, Any],
        decay: dict[str, Any],
        correlation: dict[str, Any],
        ablation: dict[str, Any],
        stability: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind descriptive summaries to experiment evidence without asserting Alpha."""
        sections = {
            "01_research_object": {
                "model": "Pangu seven-factor cross-sectional model",
                "factors": list(FACTOR_NAMES),
                "production_weights_changed": False,
            },
            "02_data_scope": {
                "data_start": spec.data_start,
                "data_end": spec.data_end,
                "data_version": spec.dataset_version,
                "dataset_label": spec.dataset_label,
                "run_count": panel_manifest["run_count"],
                "panel_hash": panel_manifest["panel_hash"],
            },
            "03_ic_analysis": metrics["ic"],
            "04_quantile_returns": self._quantile_summary(quantiles),
            "05_decay_analysis": {
                factor: {
                    "availability": values["availability"],
                    "peak_horizon": values["peak_horizon"],
                }
                for factor, values in decay["factors"].items()
            },
            "06_correlation_redundancy": {
                "redundancy_scores": correlation.get("redundancy_scores", {}),
                "review_candidates": correlation.get("redundancy_review_candidates", []),
                "automatic_factor_merge": False,
            },
            "07_ablation": {
                "full_model": ablation["models"]["full_seven_factor"],
                "contribution_order": ablation["contribution_order"],
                "automatic_weight_change": False,
            },
            "08_market_regime": {
                "availability": stability["availability"],
                "regime_source": stability.get("regime_source"),
                "regime_date_counts": {
                    regime: values["date_count"]
                    for regime, values in stability.get("regimes", {}).items()
                },
                "inferred_from_future_returns": stability.get(
                    "inferred_from_future_returns", False
                ),
            },
            "09_research_conclusions": self._conclusions(spec, decay, correlation, ablation),
            "10_risk_warnings": [
                "统计相关与样本内表现不等于可交易Alpha或未来收益。",
                "重叠未来收益、样本长度、数据覆盖和多重检验会影响统计解释。",
                "报告不能修改因子、权重、组合、风控或创建订单。",
            ],
        }
        return {
            "schema_version": config.schema_version,
            "generator_version": self.generator_version,
            "report_type": "factor_research",
            "experiment_id": spec.experiment_id,
            "run_id": run_id,
            "generated_at": utc_now(),
            "data_version": spec.dataset_version,
            "dataset_label": spec.dataset_label,
            "strategy_version": spec.strategy_version,
            "factor_version": spec.factor_version,
            "factor_contract_hash": spec.factor_contract_hash,
            "config_hash": config.config_hash,
            "panel_hash": panel_manifest["panel_hash"],
            "evidence_ids": list(spec.data_center_run_ids),
            "sections": sections,
            "metrics": metrics,
            "ai_research_analyst_interface": {
                "contract_version": "factor-report-ai-readonly-v1.0.0",
                "status": "reserved_not_implemented",
                "input_artifact": "factor_report.json",
                "permission": "evidence_explanation_only",
                "can_modify_model": False,
                "can_modify_weights": False,
                "can_create_orders": False,
            },
            "conclusion_policy": "DESCRIPTIVE_STATISTICS_ONLY",
            "investment_validity": "NOT_ESTABLISHED",
            "synthetic_warning": (
                "SYNTHETIC_TEST_ONLY仅验证数学与工程合同，不证明因子有效、收益或未来表现"
                if spec.dataset_label == "SYNTHETIC_TEST_ONLY" else None
            ),
            "production_weights_changed": False,
            "auto_weight_adjustment": False,
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
        }

    @staticmethod
    def _quantile_summary(quantiles: dict[str, Any]) -> dict[str, Any]:
        """Keep the report compact by retaining long-short and monotonicity summaries."""
        summary: dict[str, Any] = {}
        for factor, counts in quantiles["factors"].items():
            summary[factor] = {}
            for count, horizons in counts.items():
                summary[factor][count] = {
                    horizon: {
                        "long_short": values["long_short"],
                        "monotonic_low_to_high": values["monotonic_low_to_high"],
                    }
                    for horizon, values in horizons.items()
                }
        return summary

    @staticmethod
    def _conclusions(
        spec: ExperimentSpec,
        decay: dict[str, Any],
        correlation: dict[str, Any],
        ablation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return traceable observations, never effectiveness or allocation advice."""
        if spec.dataset_label == "SYNTHETIC_TEST_ONLY":
            return [{
                "type": "engineering_boundary",
                "statement": "合成数据结果只验证研究流程，不评价任何因子的投资有效性。",
            }]
        return [
            {
                "type": "decay_observation",
                "values": {
                    factor: values["peak_horizon"]
                    for factor, values in decay["factors"].items()
                },
                "statement": "记录各因子样本内绝对Rank IC峰值周期，不用于自动调仓。",
            },
            {
                "type": "redundancy_observation",
                "values": correlation.get("redundancy_review_candidates", []),
                "statement": "高相关因子对仅进入人工复核，不自动合并。",
            },
            {
                "type": "ablation_observation",
                "values": ablation["contribution_order"],
                "statement": "消融排序为样本内描述，不自动修改生产权重。",
            },
        ]
