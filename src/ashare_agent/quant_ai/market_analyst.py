from __future__ import annotations

from .contracts import (
    AnalystResult,
    ResearchAgentType,
    ResearchClaim,
    ResearchClaimLabel,
    ResearchEvidenceBundle,
)


class MarketAnalyst:
    """Use registered regime evidence and refuse unsupported market analogies."""

    agent_type = ResearchAgentType.MARKET

    def analyze(self, bundle: ResearchEvidenceBundle) -> AnalystResult:
        """Describe formal market-regime availability without inventing a bull/bear label."""
        evidence = next(
            (item for item in bundle.items if item.source_type == "registered_market_regime_evidence"),
            None,
        )
        if evidence is None:
            return AnalystResult(
                self.agent_type,
                "unavailable",
                "缺少指数趋势、市场宽度、波动率和成交量的正式点时证据。",
                (),
                ("formal_market_regime_evidence_unavailable",),
            )
        claims = (
            ResearchClaim(
                claim_id="market-regime-evidence",
                label=ResearchClaimLabel.FACT,
                title="市场环境证据状态",
                statement="仅展示已登记的市场环境分组，不生成未经证据支持的市场标签。",
                evidence_ids=(evidence.evidence_id,),
                confidence=1.0,
                metrics=dict(evidence.payload),
            ),
            ResearchClaim(
                claim_id="market-regime-hypothesis",
                label=ResearchClaimLabel.HYPOTHESIS,
                title="市场环境适配研究",
                statement="需要在预先定义的市场环境分组中检验策略和因子稳定性。",
                evidence_ids=(evidence.evidence_id,),
                confidence=0.6,
                metrics={},
            ),
        )
        return AnalystResult(
            self.agent_type, "available", "市场环境证据已读取，历史相似阶段未自动推断。",
            claims, (),
        )
