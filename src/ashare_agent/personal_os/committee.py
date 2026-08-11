from __future__ import annotations

from typing import Any, Mapping

from .contracts import content_hash, evidence_ref, stable_id


class InvestmentCommittee:
    """Run a deterministic four-role review over existing evidence."""

    def generate(
        self,
        *,
        period_start: str,
        period_end: str,
        workbench: Mapping[str, Any],
        quant_ai: Mapping[str, Any],
        strategy_validation: Mapping[str, Any],
        coach: Mapping[str, Any],
    ) -> dict[str, Any]:
        valuation = dict(workbench.get("asset_valuation") or {})
        risk_flags = list(workbench.get("risk_flags") or [])
        quant_evidence = dict(quant_ai.get("evidence") or {})
        validation_counts = dict(strategy_validation.get("counts") or {})
        payloads = [
            ("valuation", valuation),
            ("risk", {"risk_flags": risk_flags}),
            ("quant", quant_evidence),
            ("validation", validation_counts),
            ("coach", {"patterns": coach.get("patterns"), "data_gaps": coach.get("data_gaps")}),
        ]
        evidence = [
            evidence_ref(
                stable_id("E-POS-COMMITTEE", kind, content_hash(payload)),
                source_type=f"committee_{kind}", source_id=period_end, payload=payload,
            )
            for kind, payload in payloads
        ]
        gaps: list[str] = []
        if not valuation:
            gaps.append("ValuationService估值证据不可用")
        if int(quant_evidence.get("evidence_count") or 0) <= 0:
            gaps.append("没有可引用的量化验证证据")
        roles = [
            {
                "role": "价值分析师",
                "conclusion": "当前证据未提供行业中性估值分位，委员会不作低估或高估判断。",
                "evidence_ids": [evidence[2]["evidence_id"]],
            },
            {
                "role": "量化分析师",
                "conclusion": (
                    f"量化研究证据条目数为{int(quant_evidence.get('evidence_count') or 0)}；"
                    "只有通过策略准入的结果才可作为后续研究依据。"
                ),
                "evidence_ids": [evidence[2]["evidence_id"], evidence[3]["evidence_id"]],
            },
            {
                "role": "风险官",
                "conclusion": (
                    f"当前后端风险标记共{len(risk_flags)}项；存在告警时优先调查，不由委员会创建订单。"
                ),
                "evidence_ids": [evidence[1]["evidence_id"]],
            },
            {
                "role": "投资教练",
                "conclusion": "继续记录决策理由、结果与教训，避免用短期盈亏替代流程评价。",
                "evidence_ids": [evidence[4]["evidence_id"]],
            },
        ]
        return {
            "title": "每周投资委员会",
            "summary": "四角色只读审阅既有证据；结论不修改策略、风控、组合或订单。",
            "period_start": period_start,
            "period_end": period_end,
            "role_reviews": roles,
            "committee_conclusion": "保持证据先行与风险优先；任何投资动作仍需用户独立判断。",
            "data_gaps": gaps,
            "evidence": evidence,
            "status": "degraded" if gaps else "published",
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_approve_strategy": False,
        }

