from __future__ import annotations

from .contracts import (
    AnalystResult,
    ResearchAgentType,
    ResearchClaim,
    ResearchClaimLabel,
    ResearchEvidenceBundle,
)


class RiskAnalyst:
    """Interpret persisted Portfolio Risk and robustness evidence without changing limits."""

    agent_type = ResearchAgentType.RISK

    def analyze(self, bundle: ResearchEvidenceBundle) -> AnalystResult:
        """Report risk evidence availability and traceable sources without recomputation."""
        risk = next(
            (item for item in bundle.items if item.source_type == "portfolio_risk_evidence_bundle"),
            None,
        )
        robust = next(
            (item for item in bundle.items if item.source_type == "strategy_robustness_report"),
            None,
        )
        claims: list[ResearchClaim] = []
        if risk is not None:
            nested_items = risk.payload.get("evidence_items") or []
            risk_items = [
                item for item in nested_items
                if "RISK" in str(item.get("evidence_id") or "")
            ]
            claims.append(ResearchClaim(
                claim_id="risk-portfolio-snapshot",
                label=ResearchClaimLabel.FACT,
                title="组合风险快照",
                statement="行业、风格、集中度和回撤证据直接来自Portfolio & Risk Center。",
                evidence_ids=(risk.evidence_id,),
                confidence=1.0,
                metrics={"risk_snapshots": [item.get("payload") for item in risk_items]},
            ))
        if robust is not None:
            sections = robust.payload.get("sections") or {}
            claims.append(ResearchClaim(
                claim_id="risk-stress-evidence",
                label=ResearchClaimLabel.INFERENCE,
                title="压力与过拟合风险",
                statement="成本、延迟、滚动和过拟合证据共同描述策略脆弱性，不代表未来损失预测。",
                evidence_ids=(robust.evidence_id,),
                confidence=0.8,
                metrics={
                    "cost_stress": sections.get("05_cost_stress"),
                    "execution_delay": sections.get("06_execution_delay"),
                    "overfitting": sections.get("09_overfitting"),
                },
            ))
        if not claims:
            return AnalystResult(
                self.agent_type, "unavailable", "缺少组合风险与稳健性证据。", (),
                ("risk_evidence_unavailable",),
            )
        claims.append(ResearchClaim(
            claim_id="risk-concentration-question",
            label=ResearchClaimLabel.HYPOTHESIS,
            title="风险归因研究",
            statement="需要持续检验行业集中、风格暴露与换手变化是否解释风险上升。",
            evidence_ids=(claims[0].evidence_ids[0],),
            confidence=0.6,
            metrics={},
        ))
        return AnalystResult(
            self.agent_type, "available", "组合风险与策略压力证据已分层读取。",
            tuple(claims), bundle.data_gaps,
        )

