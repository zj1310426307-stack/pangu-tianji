from __future__ import annotations

from typing import Any, Mapping

from .contracts import content_hash, stable_id


SCORE_WEIGHTS = {
    "discipline": 25,
    "risk": 25,
    "research": 20,
    "records": 15,
    "review": 15,
}


class PersonalInvestmentScore:
    """Score the investment process with fixed weights, never investment return."""

    def calculate(
        self,
        *,
        as_of_date: str,
        workbench: Mapping[str, Any],
        quant_ai: Mapping[str, Any],
        counts: Mapping[str, Any],
        reports: list[Mapping[str, Any]],
        journals: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        dimensions: list[dict[str, Any]] = []
        evidence_ids: list[str] = []

        discipline = (workbench.get("discipline") or {}).get("score")
        dimensions.append(self._dimension(
            "discipline", "纪律执行", SCORE_WEIGHTS["discipline"], discipline,
            "来自模拟账户订单、成交、T+1、整手及费用审计；不评价收益",
            workbench.get("discipline") or {}, evidence_ids,
        ))

        flags = list(workbench.get("risk_flags") or [])
        if flags:
            levels = {str(item.get("level") or "") for item in flags if isinstance(item, Mapping)}
            risk_score = 30 if "danger" in levels else 70 if "warning" in levels else 100
        else:
            risk_score = None
        dimensions.append(self._dimension(
            "risk", "风险控制", SCORE_WEIGHTS["risk"], risk_score,
            "由后端风险标记映射；无风险证据时不评分",
            {"risk_flags": flags}, evidence_ids,
        ))

        quant_evidence = dict(quant_ai.get("evidence") or {})
        evidence_count = int(quant_evidence.get("evidence_count") or 0)
        latest = quant_ai.get("latest_report")
        research_score = None
        if evidence_count > 0:
            research_score = 100 if latest else 70
        dimensions.append(self._dimension(
            "research", "研究能力", SCORE_WEIGHTS["research"], research_score,
            "要求正式量化验证证据；只有证据无报告时按部分完成计分",
            {"evidence": quant_evidence, "latest_report_id": (latest or {}).get("report_id")},
            evidence_ids,
        ))

        event_count = int(counts.get("event_count") or 0)
        journal_count = int(counts.get("journal_count") or 0)
        records_score = min(100, event_count * 10 + journal_count * 20) if event_count or journal_count else None
        dimensions.append(self._dimension(
            "records", "记录能力", SCORE_WEIGHTS["records"], records_score,
            "按可审计事件与有效日志覆盖计分，未记录不以收益替代",
            {"event_count": event_count, "journal_count": journal_count}, evidence_ids,
        ))

        review_entries = sum(
            item.get("status") == "active" and item.get("entry_type") in {"review", "lesson"}
            for item in journals
        )
        review_reports = sum(
            item.get("report_type") in {"weekly_committee", "monthly_review", "personal_coach"}
            for item in reports
        )
        review_score = min(100, review_entries * 25 + review_reports * 20) if review_entries or review_reports else None
        dimensions.append(self._dimension(
            "review", "复盘能力", SCORE_WEIGHTS["review"], review_score,
            "按复盘/教训日志和个人运营报告覆盖计分",
            {"review_entries": review_entries, "review_reports": review_reports}, evidence_ids,
        ))

        total = round(sum(float(item["weighted_score"] or 0) for item in dimensions), 2)
        covered = sum(item["weight"] for item in dimensions if item["availability"] == "available")
        return {
            "as_of_date": as_of_date,
            "total_score": total,
            "coverage": covered / 100,
            "coverage_display": f"{covered}%",
            "dimensions": dimensions,
            "weights": dict(SCORE_WEIGHTS),
            "evidence_ids": evidence_ids,
            "meaning": "只评价投资流程、风险与复盘纪律，不评价盈利能力",
            "missing_weights_are_not_redistributed": True,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _dimension(
        key: str,
        label: str,
        weight: int,
        raw_score: Any,
        rule: str,
        evidence_payload: Mapping[str, Any],
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        available = isinstance(raw_score, (int, float))
        normalized = max(0.0, min(100.0, float(raw_score))) if available else None
        evidence_id = stable_id("E-POS-SCORE", key, content_hash(dict(evidence_payload)))
        evidence_ids.append(evidence_id)
        return {
            "key": key,
            "label": label,
            "weight": weight,
            "score": round(normalized, 2) if normalized is not None else None,
            "weighted_score": round(normalized * weight / 100, 2) if normalized is not None else 0,
            "availability": "available" if available else "unavailable",
            "rule": rule,
            "evidence_id": evidence_id,
        }

