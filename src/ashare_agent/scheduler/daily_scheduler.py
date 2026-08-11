from __future__ import annotations

from datetime import datetime, time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .job_manager import JobManager


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DAILY_SCHEDULER_VERSION = "daily-investment-scheduler-v1.0.0"


class DailyScheduler:
    """Expose deterministic scheduler ticks without owning a background thread.

    Windows Task Scheduler is the external wake-up mechanism. Keeping this
    class timer-free prevents a browser process and a Windows task from both
    becoming competing schedule authorities.
    """

    def __init__(
        self,
        manager: JobManager,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind the durable manager and an injectable Asia/Shanghai clock."""
        self.manager = manager
        self.registry = manager.registry
        self._now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        """Run all due slots once; durable idempotency absorbs duplicate wakes."""
        return self.manager.run_due(self._normalize(now or self._now_provider()))

    def run_job(
        self,
        job_name: str,
        *,
        now: datetime | None = None,
        trigger: str = "scheduler",
        force: bool = False,
    ) -> dict[str, Any]:
        """Run one job only when due, unless an explicit manual force is requested."""
        current = self._normalize(now or self._now_provider())
        if trigger == "scheduler" and not force:
            if job_name == "intraday_monitor" and not self._continuous_session(current):
                return self._skip(job_name, current, "当前不在A股连续交易时段，盘中监控已跳过")
            slot = self.registry.due_slot(job_name, current)
            if slot is None:
                return {
                    "version": DAILY_SCHEDULER_VERSION,
                    "state": "disabled" if not self.registry.enabled else "not_due",
                    "job_name": job_name,
                    "observed_at": current.isoformat(),
                    "can_trade": False,
                    "can_create_orders": False,
                }
        else:
            slot = current
        return self.manager.run(
            job_name,
            scheduled_for=slot,
            trigger=trigger,
            force=force,
        )

    @staticmethod
    def _continuous_session(value: datetime) -> bool:
        """Exclude lunch and non-market hours from scheduler-owned intraday work."""
        current = value.time().replace(tzinfo=None)
        return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)

    @staticmethod
    def _skip(job_name: str, current: datetime, reason: str) -> dict[str, Any]:
        """Return a fail-closed non-trading skip before any report/model call."""
        return {
            "version": DAILY_SCHEDULER_VERSION,
            "state": "skipped",
            "job_name": job_name,
            "observed_at": current.isoformat(),
            "reason": reason,
            "can_trade": False,
            "can_create_orders": False,
        }

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        """Return configured schedules and currently due slots without dispatching work."""
        current = self._normalize(now or self._now_provider())
        due = [
            {
                "job_name": job.job_name,
                "report_type": job.report_type.value,
                "scheduled_for": slot.isoformat(),
            }
            for job, slot in self.registry.due_jobs(current)
        ]
        return {
            "version": DAILY_SCHEDULER_VERSION,
            "enabled": self.registry.enabled,
            "observed_at": current.isoformat(),
            "jobs": self.registry.definitions(),
            "due": due,
            "execution_model": "external_windows_wakeup_single_tick",
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _normalize(value: datetime) -> datetime:
        """Normalize naive test clocks and aware runtime clocks to Shanghai time."""
        if value.tzinfo is None:
            return value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)
