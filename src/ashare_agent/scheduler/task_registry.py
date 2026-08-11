from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Mapping

from ..core.contracts import InvestmentReportType, ScheduledJobDefinition


TASK_REGISTRY_VERSION = "daily-os-task-registry-v1.0.0"


class TaskRegistry:
    """Own immutable operating schedules without starting threads or orders."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Validate optional schedule overrides and build the four required jobs."""
        raw = dict(config or {})
        interval = int(raw.get("intraday_interval_minutes", 60))
        if interval not in {30, 60}:
            raise ValueError("daily_investment_os.intraday_interval_minutes只允许30或60")
        self.enabled = bool(raw.get("enabled", True))
        self.jobs = {
            "morning_report": ScheduledJobDefinition(
                job_name="morning_report",
                report_type=InvestmentReportType.MORNING_REPORT,
                label="盘古晨报",
                schedule_kind="daily",
                time_of_day=str(raw.get("morning_time", "08:45")),
            ),
            "intraday_monitor": ScheduledJobDefinition(
                job_name="intraday_monitor",
                report_type=InvestmentReportType.INTRADAY_MONITOR,
                label="盘中风险监控",
                schedule_kind="interval",
                interval_minutes=interval,
                window_start=str(raw.get("intraday_start", "09:45")),
                window_end=str(raw.get("intraday_end", "14:45")),
                grace_minutes=min(20, interval - 1),
            ),
            "closing_review": ScheduledJobDefinition(
                job_name="closing_review",
                report_type=InvestmentReportType.CLOSING_REVIEW,
                label="盘后投资复盘",
                schedule_kind="daily",
                time_of_day=str(raw.get("closing_time", "15:30")),
            ),
            "weekly_report": ScheduledJobDefinition(
                job_name="weekly_report",
                report_type=InvestmentReportType.WEEKLY_REPORT,
                label="盘古周报",
                schedule_kind="weekly",
                weekday=int(raw.get("weekly_weekday", 4)),
                time_of_day=str(raw.get("weekly_time", "16:00")),
            ),
        }
        self._validate()

    def _validate(self) -> None:
        """Reject invalid times and any job that claims order capability."""
        for job in self.jobs.values():
            for value in (job.time_of_day, job.window_start, job.window_end):
                if value is not None:
                    time.fromisoformat(value)
            if job.weekday is not None and not 0 <= job.weekday <= 6:
                raise ValueError("daily_investment_os.weekly_weekday必须在0到6之间")
            if job.can_create_orders:
                raise ValueError("Daily Investment OS任务禁止创建订单")

    def get(self, job_name: str) -> ScheduledJobDefinition:
        """Return one registered job or reject unknown scheduler input."""
        try:
            return self.jobs[job_name]
        except KeyError as exc:
            raise ValueError("不支持的Daily Investment OS任务") from exc

    def definitions(self) -> list[dict[str, Any]]:
        """Serialize schedules for API and dashboard display."""
        return [
            {
                "job_name": job.job_name,
                "report_type": job.report_type.value,
                "label": job.label,
                "schedule_kind": job.schedule_kind,
                "time_of_day": job.time_of_day,
                "weekday": job.weekday,
                "interval_minutes": job.interval_minutes,
                "window_start": job.window_start,
                "window_end": job.window_end,
                "grace_minutes": job.grace_minutes,
                "can_create_orders": False,
            }
            for job in self.jobs.values()
        ]

    def due_slot(self, job_name: str, now: datetime) -> datetime | None:
        """Return the current deterministic slot when a job is due within grace."""
        if not self.enabled:
            return None
        job = self.get(job_name)
        if job.schedule_kind in {"daily", "weekly"}:
            if job.schedule_kind == "weekly" and now.weekday() != job.weekday:
                return None
            scheduled = datetime.combine(
                now.date(), time.fromisoformat(job.time_of_day or "00:00"), now.tzinfo
            )
            return scheduled if scheduled <= now <= scheduled + timedelta(minutes=job.grace_minutes) else None
        current_time = now.time().replace(tzinfo=None)
        if time(11, 30) < current_time < time(13, 0) or current_time > time(15, 0):
            return None
        start = datetime.combine(
            now.date(), time.fromisoformat(job.window_start or "00:00"), now.tzinfo
        )
        end = datetime.combine(
            now.date(), time.fromisoformat(job.window_end or "00:00"), now.tzinfo
        )
        if not start <= now <= end + timedelta(minutes=job.grace_minutes):
            return None
        interval = int(job.interval_minutes or 60)
        slot_number = max(0, int((now - start).total_seconds() // (interval * 60)))
        slot = start + timedelta(minutes=slot_number * interval)
        if slot > end or now > slot + timedelta(minutes=job.grace_minutes):
            return None
        return slot

    def due_jobs(self, now: datetime) -> list[tuple[ScheduledJobDefinition, datetime]]:
        """List all due slots; idempotency remains JobManager's responsibility."""
        due: list[tuple[ScheduledJobDefinition, datetime]] = []
        for name, job in self.jobs.items():
            slot = self.due_slot(name, now)
            if slot is not None:
                due.append((job, slot))
        return due
