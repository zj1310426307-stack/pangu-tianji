from __future__ import annotations

from typing import Any

from .contracts import (
    AnalystResult,
    ResearchAgentType,
    ResearchClaim,
    ResearchClaimLabel,
    ResearchEvidenceBundle,
)


class StrategyAnalyst:
    """Explain validation state and observed strategy strengths without promotion power."""

    agent_type = ResearchAgentType.STRATEGY

    def analyze(self, bundle: ResearchEvidenceBundle) -> AnalystResult:
        """Build cited strategy claims from Validation, Factor and Robustness reports."""
        validation = self._item(bundle, "strategy_validation_report")
        if validation is None:
            return AnalystResult(
                self.agent_type, "unavailable", "缺少策略准入报告，无法分析策略状态。",
                (), ("strategy_validation_report_unavailable",),
            )
        payload = dict(validation.payload)
        overview = (payload.get("sections") or {}).get("01_strategy_overview") or {}
        recommendation = (payload.get("sections") or {}).get("06_committee_recommendation") or {}
        claims = [ResearchClaim(
            claim_id="strategy-current-state",
            label=ResearchClaimLabel.FACT,
            title="策略生命周期状态",
            statement="当前状态来自已封存的策略准入委员会报告。",
            evidence_ids=(validation.evidence_id,),
            confidence=1.0,
            metrics={
                "current_state": overview.get("current_state"),
                "recommended_state": recommendation.get("recommended_state"),
                "system_recommendation": recommendation.get("system_recommendation"),
            },
        )]
        factor = self._item(bundle, "factor_research_report")
        if factor is not None:
            contribution = (
                ((factor.payload.get("sections") or {}).get("07_ablation") or {})
                .get("contribution_order") or []
            )
            claims.append(ResearchClaim(
                claim_id="strategy-factor-contribution",
                label=ResearchClaimLabel.INFERENCE,
                title="样本内因子贡献结构",
                statement="因子消融排序可用于识别待复核的贡献来源，但不能证明未来有效。",
                evidence_ids=(factor.evidence_id,),
                confidence=0.75,
                metrics={"contribution_order": contribution},
            ))
        robust = self._item(bundle, "strategy_robustness_report")
        if robust is not None:
            score = ((robust.payload.get("sections") or {}).get("10_robustness_score") or {})
            claims.append(ResearchClaim(
                claim_id="strategy-robustness-status",
                label=ResearchClaimLabel.FACT,
                title="稳健性证据状态",
                statement="稳健性得分与缺失组件按注册报告原值展示，不用于自动选择参数。",
                evidence_ids=(robust.evidence_id,),
                confidence=1.0,
                metrics={
                    "score": score.get("score"),
                    "grade": score.get("grade"),
                    "missing_components": score.get("missing_components", []),
                },
            ))
        claims.append(ResearchClaim(
            claim_id="strategy-next-research",
            label=ResearchClaimLabel.HYPOTHESIS,
            title="下一步研究假设",
            statement="应在独立样本外和前向模拟阶段复核当前样本内结论是否保持。",
            evidence_ids=(validation.evidence_id,),
            confidence=0.65,
            metrics={},
        ))
        return AnalystResult(
            self.agent_type, "available", "策略状态、贡献线索与后续验证边界已读取。",
            tuple(claims), bundle.data_gaps,
        )

    @staticmethod
    def _item(bundle: ResearchEvidenceBundle, source_type: str):
        return next((item for item in bundle.items if item.source_type == source_type), None)

