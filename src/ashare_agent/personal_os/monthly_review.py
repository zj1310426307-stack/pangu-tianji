from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .contracts import content_hash, evidence_ref, stable_id


class MonthlyInvestmentReview:
    """Build a monthly review from persisted paper NAV and process evidence."""

    def generate(
        self,
        *,
        period_start: str,
        period_end: str,
        workbench: Mapping[str, Any],
        events: list[Mapping[str, Any]],
        journals: list[Mapping[str, Any]],
        score: Mapping[str, Any],
        coach: Mapping[str, Any],
    ) -> dict[str, Any]:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
        nav = [
            item for item in list(workbench.get("nav") or [])
            if item.get("trade_date") and start <= date.fromisoformat(str(item["trade_date"])[:10]) <= end
        ]
        nav.sort(key=lambda item: str(item.get("trade_date")))
        monthly_return = None
        if len(nav) >= 2 and float(nav[0].get("equity") or 0) > 0:
            monthly_return = float(nav[-1]["equity"]) / float(nav[0]["equity"]) - 1
        scoped_events = [item for item in events if start <= date.fromisoformat(str(item["trade_date"])) <= end]
        scoped_journals = [item for item in journals if start <= date.fromisoformat(str(item["trade_date"])) <= end]
        attribution = list(workbench.get("symbol_attribution") or [])
        evidence_payloads = {
            "nav": {"period_start": period_start, "period_end": period_end, "nav": nav},
            "process": {"event_count": len(scoped_events), "journal_count": len(scoped_journals)},
            "score": {"total_score": score.get("total_score"), "coverage": score.get("coverage")},
            "attribution": {"scope": "cumulative_account", "items": attribution},
        }
        evidence = [
            evidence_ref(
                stable_id("E-POS-MONTH", key, content_hash(payload)),
                source_type=f"monthly_{key}", source_id=period_end, payload=payload,
            )
            for key, payload in evidence_payloads.items()
        ]
        gaps = ["缺少点时基准月收益，不能计算月度超额收益"]
        if monthly_return is None:
            gaps.append("本月持久化净值少于2个有效点，不能计算月收益")
        if attribution:
            gaps.append("个股归因为账户累计口径，不能冒充月度个股贡献")
        return {
            "title": "月度投资复盘",
            "summary": "复盘收益证据、风险纪律、记录质量与改进项；不以盈利替代过程评分。",
            "period_start": period_start,
            "period_end": period_end,
            "performance": {
                "monthly_return": monthly_return,
                "benchmark_return": None,
                "excess_return": None,
                "source": "ValuationService支持的持久化模拟账户NAV",
            },
            "process": {
                "event_count": len(scoped_events),
                "journal_count": len(scoped_journals),
                "personal_score": score.get("total_score"),
                "score_coverage": score.get("coverage"),
            },
            "cumulative_symbol_attribution": attribution,
            "errors_and_biases": list(coach.get("patterns") or []),
            "improvements": list(coach.get("improvements") or []),
            "data_gaps": gaps,
            "evidence": evidence,
            "status": "degraded",
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

