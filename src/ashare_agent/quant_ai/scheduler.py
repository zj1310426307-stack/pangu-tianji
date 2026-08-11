from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .service import AIQuantResearchService


QUANT_AI_SCHEDULER_VERSION = "quant-ai-scheduler-v1.0.0"


class ResearchAnalystScheduler:
    """Own the 08:00 research cadence without owning agents, strategies or orders."""

    def __init__(
        self,
        service: AIQuantResearchService,
        *,
        trading_day_provider: Callable[[date], bool] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.service = service
        self.calendar_source = (
            "injected_trading_calendar"
            if trading_day_provider is not None
            else "weekday_fallback"
        )
        self.trading_day_provider = trading_day_provider or (lambda value: value.weekday() < 5)
        self.now_provider = now_provider or (
            lambda: datetime.now(ZoneInfo("Asia/Shanghai"))
        )

    def due_slot(self, now: datetime | None = None) -> datetime | None:
        """Return today's 08:00 slot only during the grace window; never backfill."""
        current = now or self.now_provider()
        if not self.trading_day_provider(current.date()):
            return None
        slot = datetime.combine(current.date(), time(8, 0), current.tzinfo)
        return slot if slot <= current <= slot + timedelta(minutes=30) else None

    def tick(self, now: datetime | None = None) -> dict:
        """Run one due slot idempotently or report an explicit no-op."""
        current = now or self.now_provider()
        slot = self.due_slot(current)
        if slot is None:
            return {
                "state": "not_due",
                "now": current.isoformat(),
                "can_trade": False,
                "can_create_orders": False,
            }
        report = self.service.generate(
            scheduled_for=slot.isoformat(), trigger="scheduler"
        )
        return {
            "state": "succeeded",
            "scheduled_for": slot.isoformat(),
            "report": report,
            "can_trade": False,
            "can_create_orders": False,
        }

    def run_now(self) -> dict:
        """Generate one explicit user-action brief without altering the schedule."""
        now = self.now_provider()
        report = self.service.generate(
            scheduled_for=now.isoformat(), trigger="user_action"
        )
        return {
            "state": "succeeded",
            "scheduled_for": now.isoformat(),
            "report": report,
            "can_trade": False,
            "can_create_orders": False,
        }

    def status(self) -> dict:
        """Describe cadence and current due state without running a report."""
        now = self.now_provider()
        slot = self.due_slot(now)
        return {
            "scheduler_version": QUANT_AI_SCHEDULER_VERSION,
            "job_name": "ai_quant_morning_brief",
            "time_of_day": "08:00",
            "timezone": "Asia/Shanghai",
            "trading_days_only": True,
            "calendar_source": self.calendar_source,
            "official_holiday_safe": self.calendar_source == "injected_trading_calendar",
            "due_slot": slot.isoformat() if slot else None,
            "missed_slots_backfilled": False,
            "can_trade": False,
            "can_create_orders": False,
        }
