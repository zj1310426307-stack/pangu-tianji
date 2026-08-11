from __future__ import annotations

from typing import Any, Mapping

from ..quant_lab.contracts import sha256_json, utc_now
from .contracts import EVOLUTION_SCHEMA_VERSION, safety_contract, stable_id


class StrategyEvolutionReportGenerator:
    """Generate one evidence-linked Strategy Health Report and observer questions."""

    generator_version = "strategy-evolution-report-v1.0.0"

    def generate(
        self,
        *,
        branch: Mapping[str, Any],
        observation: Mapping[str, Any],
        health: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Explain monitor results without recommending an automatic strategy change."""
        questions = self._questions(health)
        report_id = stable_id(
            "evolution-report", branch.get("strategy_id"), branch.get("version"),
            observation.get("evidence_hash"),
        )
        report = {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "generator_version": self.generator_version,
            "report_type": "strategy_health_report",
            "report_id": report_id,
            "strategy_id": branch.get("strategy_id"),
            "strategy_version": branch.get("version"),
            "branch_name": branch.get("branch_name"),
            "generated_at": utc_now(),
            "evidence_ids": list(observation.get("evidence_ids") or []),
            "evidence_hash": observation.get("evidence_hash"),
            "sections": {
                "01_strategy_identity": {
                    "strategy_name": branch.get("strategy_name"),
                    "version": branch.get("version"),
                    "parent_version": branch.get("parent_version"),
                    "code_hash": branch.get("code_hash"),
                    "parameter_hash": branch.get("parameter_hash"),
                    "factor_version": branch.get("factor_version"),
                    "data_version": branch.get("data_version"),
                    "lifecycle_state": branch.get("lifecycle_state"),
                },
                "02_evidence_scope": {
                    "review_id": observation.get("review_id"),
                    "observed_at": observation.get("observed_at"),
                    "dataset_label": observation.get("dataset_label"),
                    "data_gaps": list(observation.get("data_gaps") or []),
                },
                "03_strategy_health": {
                    "score": health.get("score"),
                    "partial_score": health.get("partial_score"),
                    "coverage": health.get("coverage"),
                    "status": health.get("status"),
                    "components": health.get("components"),
                    "weights": health.get("weights"),
                },
                "04_performance_and_risk": {
                    "performance": health.get("performance"),
                    "risk": health.get("risk"),
                    "decay": health.get("decay"),
                },
                "05_factor_drift": health.get("factor_drift"),
                "06_execution_and_environment": {
                    "execution": health.get("execution"),
                    "environment": health.get("environment"),
                },
                "07_strategy_observer": {
                    "research_questions": questions,
                    "recommendation": "HUMAN_RESEARCH_REVIEW_ONLY" if questions else "CONTINUE_OBSERVATION",
                    "automatic_experiment_created": False,
                    "production_change_proposed": False,
                },
                "08_safety": {
                    **safety_contract(),
                    "production_candidate_is_live_trading": False,
                    "profit_guarantee": False,
                },
            },
            "ai_strategy_observer_interface": {
                "contract_version": "strategy-observer-readonly-v1.0.0",
                "status": "deterministic_questions_available",
                "permission": "evidence_summary_and_research_questions_only",
                "questions": questions,
                **safety_contract(),
            },
            "used_for_execution": False,
            **safety_contract(),
        }
        report["report_hash"] = sha256_json(report)
        return report

    @staticmethod
    def _questions(health: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Turn deterministic warnings into bounded questions, never experiments."""
        questions: list[dict[str, Any]] = []
        decay = health.get("decay") or {}
        for issue in decay.get("issues") or []:
            questions.append({
                "type": "strategy_decay",
                "subject": issue.get("metric"),
                "question": f"研究{issue.get('metric')}相对历史基线恶化的原因与市场环境依赖。",
                "can_launch_experiment": False,
            })
        for factor in (health.get("factor_drift") or {}).get("factors") or []:
            if factor.get("status") in {"WATCH", "DECAYING"}:
                questions.append({
                    "type": "factor_drift",
                    "subject": factor.get("factor_name"),
                    "question": f"复核{factor.get('factor_name')}因子的IC、分布和贡献变化。",
                    "can_launch_experiment": False,
                })
        if decay.get("status") == "BASELINE_BUILDING":
            questions.append({
                "type": "evidence_gap",
                "subject": "historical_baseline",
                "question": "继续积累封存观察，达到两个独立时点后再判断策略衰减。",
                "can_launch_experiment": False,
            })
        return questions[:20]

