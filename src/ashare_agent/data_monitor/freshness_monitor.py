from __future__ import annotations

from datetime import datetime, time
from typing import Any, Mapping

from .contracts import component, issue


class DataFreshnessMonitor:
    """Verify point-in-time dates without comparing historical runs to wall-clock now."""

    def evaluate(self, manifest: Mapping[str, Any], *, preview: bool) -> dict[str, Any]:
        """Check that data_time belongs to research_date and formal data is after close."""
        problems: list[dict[str, Any]] = []
        research_date = str(manifest.get("research_date") or "")
        raw_time = str(manifest.get("data_time") or "")
        observed: datetime | None = None
        try:
            observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            problems.append(issue(
                "freshness", "INVALID_DATA_TIME", "BLOCKED",
                "Data Center data_time无法解析", blocking=True,
                details={"data_time": raw_time},
            ))
        same_date = bool(observed and observed.date().isoformat() == research_date)
        if observed and not same_date:
            problems.append(issue(
                "freshness", "TRADE_DATE_MISMATCH", "BLOCKED",
                "行情时间与研究日期不一致", blocking=True,
                details={"research_date": research_date, "data_time": raw_time},
            ))
        after_close = bool(observed and observed.timetz().replace(tzinfo=None) >= time(15, 0))
        if observed and not preview and same_date and not after_close:
            problems.append(issue(
                "freshness", "FORMAL_SNAPSHOT_BEFORE_CLOSE", "BLOCKED",
                "正式收盘研究使用了15:00前的行情快照", blocking=True,
                details={"data_time": raw_time},
            ))
        score = 100
        if observed is None:
            score = 0
        elif not same_date:
            score = 0
        elif not preview and not after_close:
            score = 20
        checks = {
            "research_date": research_date,
            "data_time": raw_time,
            "same_trade_date": same_date,
            "after_close": after_close,
            "preview": bool(preview),
        }
        return component("freshness", score, checks, problems)
