from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4

from ..quant_lab.contracts import sha256_json, utc_now
from .contracts import AnalystResult, ResearchEvidenceBundle, ResearchReportStatus


RESEARCH_BRIEF_GENERATOR_VERSION = "quant-ai-report-generator-v1.0.0"


class ResearchReportGenerator:
    """Assemble a cited daily brief while keeping model prose non-executable."""

    def generate(
        self,
        *,
        bundle: ResearchEvidenceBundle,
        analyst_results: Sequence[AnalystResult],
        deterministic_evaluation: Mapping[str, Any],
        model_analysis: Mapping[str, Any] | None,
        model_evaluation: Mapping[str, Any] | None,
        model_version: str,
        previous_report: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one immutable Research Brief from verified source-owned evidence."""
        agents = {item.agent_type.value: item.as_dict() for item in analyst_results}
        model_grounded = bool(model_evaluation and model_evaluation.get("grounded"))
        status = (
            ResearchReportStatus.PUBLISHED
            if model_grounded and deterministic_evaluation.get("grounded")
            else ResearchReportStatus.DEGRADED
        )
        hypotheses = [
            claim
            for result in agents.values()
            for claim in result.get("claims", [])
            if claim.get("label") == "HYPOTHESIS"
        ]
        questions = [
            {
                "source_claim_id": claim["claim_id"],
                "question": claim["title"],
                "rationale": claim["statement"],
                "priority": "HIGH" if claim["claim_id"].startswith("risk-") else "MEDIUM",
                "evidence_ids": list(claim["evidence_ids"]),
                "related_experiment": bundle.experiment_id,
                "can_launch_experiment": False,
            }
            for claim in hypotheses
        ]
        report = {
            "schema_version": "pangu-ai-quant-research-report-v1.0.0",
            "generator_version": RESEARCH_BRIEF_GENERATOR_VERSION,
            "report_id": f"qreport-{uuid4().hex}",
            "report_type": "daily_research_brief",
            "agent_type": "research_committee",
            "created_time": utc_now(),
            "strategy_id": bundle.strategy_id,
            "strategy_version": bundle.strategy_version,
            "experiment_id": bundle.experiment_id,
            "research_run_id": bundle.research_run_id,
            "dataset_label": bundle.dataset_label,
            "status": status.value,
            "model_version": model_version,
            "evidence_hash": bundle.evidence_hash,
            "evidence_ids": [item.evidence_id for item in bundle.items],
            "sections": {
                "01_strategy_status": agents.get("strategy_analyst", {}),
                "02_factor_change": {
                    "analysis": agents.get("factor_analyst", {}),
                    "change_since_previous": self._change(previous_report, bundle.evidence_hash, "factor"),
                },
                "03_risk_change": {
                    "analysis": agents.get("risk_analyst", {}),
                    "change_since_previous": self._change(previous_report, bundle.evidence_hash, "risk"),
                },
                "04_market_context": agents.get("market_analyst", {}),
                "05_research_recommendations": {
                    "questions": questions,
                    "research_only": True,
                    "automatic_experiment_launch": False,
                    "automatic_parameter_change": False,
                },
            },
            "model_analysis": (
                dict(model_analysis) if model_grounded else {
                    "availability": "unavailable_or_rejected",
                    "summary": "模型不可用或输出未通过证据校验；仅发布确定性研究摘要。",
                    "conclusions": [],
                }
            ),
            "evaluation": {
                "deterministic": dict(deterministic_evaluation),
                "model": dict(model_evaluation or {
                    "grounded": False,
                    "reason": "model_unavailable",
                }),
                "publication_policy": "MODEL_CONTENT_ONLY_IF_GROUNDED",
            },
            "data_gaps": list(bundle.data_gaps),
            "synthetic_warning": (
                "SYNTHETIC_TEST_ONLY只验证AI研究合同，不形成正式研究结论"
                if bundle.dataset_label == "SYNTHETIC_TEST_ONLY" else None
            ),
            "permission": "RESEARCH_READ_ONLY",
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_experiment": False,
            "can_modify_parameters": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_approve_strategy": False,
        }
        report["report_hash"] = sha256_json(report)
        return report

    @staticmethod
    def _change(
        previous_report: Mapping[str, Any] | None,
        evidence_hash: str,
        domain: str,
    ) -> dict[str, Any]:
        """State change availability without fabricating time-series attribution."""
        if not previous_report:
            return {
                "availability": "unavailable",
                "reason": f"previous_{domain}_research_snapshot_unavailable",
            }
        previous_hash = str(previous_report.get("evidence_hash") or "")
        return {
            "availability": "evidence_changed" if previous_hash != evidence_hash else "unchanged",
            "previous_report_id": previous_report.get("report_id"),
            "metric_change_computed": False,
            "reason": "点时可比指标尚未形成，不输出未经验证的变化数字",
        }


MODEL_SYSTEM_PROMPT = """你是盘古·天机AI量化研究员。你只能总结输入中的已引用证据。
返回严格JSON：summary字符串、summary_evidence_ids数组；conclusions数组，每项必须包含claim_id、label、title、statement、evidence_ids、metrics。
label只能是FACT、INFERENCE、HYPOTHESIS。每项至少引用一个输入evidence_id。
不得创造数字、历史相似阶段、收益承诺、参数调整、策略晋级、仓位、订单或交易建议。
不得输出密钥。你没有修改实验、参数、因子、组合、风控、审批或交易的权限。"""
