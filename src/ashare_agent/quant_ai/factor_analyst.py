from __future__ import annotations

from .contracts import (
    AnalystResult,
    ResearchAgentType,
    ResearchClaim,
    ResearchClaimLabel,
    ResearchEvidenceBundle,
)


class FactorAnalyst:
    """Summarize registered IC and ablation evidence without changing factor weights."""

    agent_type = ResearchAgentType.FACTOR

    def analyze(self, bundle: ResearchEvidenceBundle) -> AnalystResult:
        """Describe every registered factor using one stable horizon snapshot."""
        evidence = next(
            (item for item in bundle.items if item.source_type == "factor_research_report"),
            None,
        )
        if evidence is None:
            return AnalystResult(
                self.agent_type, "unavailable", "缺少因子研究报告。", (),
                ("factor_report_unavailable",),
            )
        report = dict(evidence.payload)
        ic = ((report.get("sections") or {}).get("03_ic_analysis") or {})
        claims: list[ResearchClaim] = []
        for factor_name in sorted(ic):
            horizons = ic.get(factor_name) or {}
            selected_key = "20" if "20" in horizons else next(iter(horizons), None)
            if selected_key is None:
                continue
            metrics = dict(horizons[selected_key] or {})
            metrics["horizon"] = selected_key
            claims.append(ResearchClaim(
                claim_id=f"factor-{factor_name}",
                label=ResearchClaimLabel.FACT,
                title=f"{factor_name} 因子统计",
                statement="IC、ICIR、胜率和样本数来自注册因子报告，属于描述性统计。",
                evidence_ids=(evidence.evidence_id,),
                confidence=1.0,
                metrics=metrics,
            ))
        redundancy = (
            ((report.get("sections") or {}).get("06_correlation_redundancy") or {})
            .get("review_candidates") or []
        )
        claims.append(ResearchClaim(
            claim_id="factor-redundancy-review",
            label=ResearchClaimLabel.INFERENCE,
            title="因子冗余复核",
            statement="高相关候选只进入人工研究问题池，不触发自动合并或降权。",
            evidence_ids=(evidence.evidence_id,),
            confidence=0.8,
            metrics={"review_candidates": redundancy},
        ))
        claims.append(ResearchClaim(
            claim_id="factor-stability-question",
            label=ResearchClaimLabel.HYPOTHESIS,
            title="因子稳定性假设",
            statement="需要按未来独立窗口持续检验因子方向、衰减和分层单调性。",
            evidence_ids=(evidence.evidence_id,),
            confidence=0.65,
            metrics={},
        ))
        return AnalystResult(
            self.agent_type,
            "available" if claims else "partial",
            "因子统计、冗余与后续稳定性问题已读取。",
            tuple(claims),
            () if claims else ("factor_metrics_unavailable",),
        )
