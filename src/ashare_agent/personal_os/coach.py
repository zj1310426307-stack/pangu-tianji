from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from .contracts import content_hash, evidence_ref, stable_id


class PersonalInvestmentCoach:
    """Explain evidenced behavior patterns without prescribing or executing trades."""

    def analyze(
        self,
        *,
        period_start: str,
        period_end: str,
        profile: Mapping[str, Any],
        events: list[Mapping[str, Any]],
        journals: list[Mapping[str, Any]],
        score: Mapping[str, Any],
    ) -> dict[str, Any]:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
        scoped_events = [item for item in events if start <= date.fromisoformat(str(item["trade_date"])) <= end]
        scoped_journals = [item for item in journals if start <= date.fromisoformat(str(item["trade_date"])) <= end]
        trades = [item for item in scoped_events if item.get("event_type") in {"BUY", "SELL"}]
        reflections = [item for item in scoped_journals if item.get("entry_type") in {"review", "lesson"}]
        evidence_payloads = {
            "profile": {
                "profile_id": profile.get("profile_id"),
                "revision": profile.get("revision"),
                "behavior": list(profile.get("behavior") or []),
            },
            "events": {"count": len(scoped_events), "trade_count": len(trades)},
            "journals": {"count": len(scoped_journals), "reflection_count": len(reflections)},
            "score": {
                "as_of_date": score.get("as_of_date"),
                "total_score": score.get("total_score"),
                "coverage": score.get("coverage"),
            },
        }
        evidence = [
            evidence_ref(
                stable_id("E-POS-COACH", key, content_hash(payload)),
                source_type=f"personal_os_{key}",
                source_id=str(payload.get("profile_id") or payload.get("as_of_date") or period_end),
                payload=payload,
            )
            for key, payload in evidence_payloads.items()
        ]
        patterns: list[dict[str, Any]] = []
        if len(trades) >= 20 and (end - start).days <= 31:
            patterns.append({
                "label": "FACT",
                "pattern": "高频操作迹象",
                "statement": f"本期记录了{len(trades)}笔模拟成交，达到个人教练的高频复核阈值。",
                "evidence_ids": [evidence[1]["evidence_id"]],
            })
        for behavior in list(profile.get("behavior") or []):
            patterns.append({
                "label": "USER_DECLARED",
                "pattern": "用户自述行为",
                "statement": str(behavior),
                "evidence_ids": [evidence[0]["evidence_id"]],
            })
        gaps: list[str] = []
        if not trades:
            gaps.append("本期没有可复核的模拟成交事件，不能判断交易行为模式")
        if not reflections:
            gaps.append("本期没有复盘或教训日志，不能判断错误是否被纠正")
        if float(score.get("coverage") or 0) < 1:
            gaps.append("个人投资评分证据覆盖不足，缺失维度未重新分配权重")
        improvements = []
        if not reflections:
            improvements.append("每次重要决策后补充结果与教训，形成可回放闭环")
        if any(item.get("pattern") == "高频操作迹象" for item in patterns):
            improvements.append("逐笔核对操作是否来自既定研究计划，并记录未执行的替代方案")
        if not improvements:
            improvements.append("维持证据先行、风险优先和定期复盘纪律")
        return {
            "title": "个人投资教练分析",
            "summary": "基于已记录事件、日志与流程评分给出行为反馈；不根据收益倒推决策质量。",
            "period_start": period_start,
            "period_end": period_end,
            "patterns": patterns,
            "improvements": improvements,
            "data_gaps": gaps,
            "evidence": evidence,
            "status": "degraded" if gaps else "published",
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

