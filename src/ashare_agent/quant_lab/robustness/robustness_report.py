from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..contracts import ExperimentSpec, utc_now
from .contracts import RobustnessConfig


class RobustnessReportGenerator:
    """Build one structured evidence report without producing strategy advice or promotion."""

    generator_version = "strategy-robustness-report-v1.0.0"

    def generate(
        self,
        *,
        spec: ExperimentSpec,
        run_id: str,
        config: RobustnessConfig,
        scenario_manifest: dict[str, Any],
        parameter: dict[str, Any],
        cost: dict[str, Any],
        delay: dict[str, Any],
        regime: dict[str, Any],
        bootstrap: dict[str, Any],
        rolling: dict[str, Any],
        overfitting: dict[str, Any],
        score: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble fixed report sections and explicitly expose unavailable evidence."""
        evidence_ids = [
            {"type": "data_center_run", "id": value}
            for value in spec.data_center_run_ids
        ] + [
            {"type": "strategy_path", "id": item["scenario_id"], "hash": item["path_hash"]}
            for item in scenario_manifest["scenarios"]
        ]
        synthetic_warning = (
            "SYNTHETIC_TEST_ONLY仅验证稳健性数学、状态和证据合同，不证明策略稳健、"
            "存在Alpha或适合模拟/实盘"
            if spec.dataset_label == "SYNTHETIC_TEST_ONLY" else None
        )
        sections = {
            "01_research_object": {
                "strategy_version": spec.strategy_version,
                "factor_version": spec.factor_version,
                "objective": "stress_stability_not_return_maximization",
            },
            "02_data_and_scenario_scope": {
                "data_start": spec.data_start,
                "data_end": spec.data_end,
                "dataset_version": spec.dataset_version,
                "dataset_label": spec.dataset_label,
                "scenario_count": scenario_manifest["scenario_count"],
                "scenario_runner_versions": scenario_manifest["engine_versions"],
            },
            "03_baseline": parameter["baseline"],
            "04_parameter_sensitivity": parameter,
            "05_cost_stress": cost,
            "06_execution_delay": delay,
            "07_market_regime": regime,
            "08_bootstrap_and_rolling": {"bootstrap": bootstrap, "rolling": rolling},
            "09_overfitting": overfitting,
            "10_robustness_score": score,
            "11_research_conclusion": {
                "investment_validity": "NOT_ESTABLISHED",
                "strategy_admission": "NOT_EVALUATED",
                "best_parameter_selected": False,
                "production_change_recommended": False,
            },
            "12_risk_warnings": {
                "synthetic_warning": synthetic_warning,
                "score_is_descriptive_only": True,
                "missing_evidence": score.get("missing_components", []),
                "requires_independent_holdout_and_forward_test": True,
            },
        }
        return {
            "schema_version": config.schema_version,
            "report_type": "strategy_robustness",
            "generator_version": self.generator_version,
            "generated_at": utc_now(),
            "experiment_id": spec.experiment_id,
            "run_id": run_id,
            "dataset_version": spec.dataset_version,
            "dataset_label": spec.dataset_label,
            "strategy_version": spec.strategy_version,
            "factor_version": spec.factor_version,
            "factor_contract_hash": spec.factor_contract_hash,
            "config_hash": config.config_hash,
            "config": asdict(config),
            "sections": sections,
            "evidence_ids": evidence_ids,
            "synthetic_warning": synthetic_warning,
            "investment_validity": "NOT_ESTABLISHED",
            "strategy_admission_decision": "NOT_EVALUATED",
            "ai_research_agent_interface": {
                "status": "reserved_not_implemented",
                "input_contract": "robustness_report.json",
                "permission": "evidence_explanation_only",
                "can_modify_strategy": False,
                "can_select_parameters": False,
                "can_promote_strategy": False,
                "can_create_orders": False,
            },
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_promote_strategy": False,
        }
