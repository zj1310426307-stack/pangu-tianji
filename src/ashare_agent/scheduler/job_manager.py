from __future__ import annotations

from datetime import date, datetime
import re
import sqlite3
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from ..services.investment_report_center import (
    InvestmentReportCenter,
    InvestmentReportCenterError,
)
from .task_registry import TaskRegistry


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
JOB_MANAGER_VERSION = "daily-os-job-manager-v1.0.0"
_SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|api[_ -]?key\s*[:=]|bearer\s+[A-Za-z0-9._-]+)",
    re.I,
)


class OperatingReportService(Protocol):
    """Describe the only business operation the scheduler may invoke."""

    def generate_report(
        self,
        report_type: str,
        trade_date: str | None = None,
        scheduled_for: str | None = None,
    ) -> Mapping[str, Any]:
        """Generate and persist one evidence-backed, non-trading report."""


class JobManager:
    """Claim, run, and finalize Daily Investment OS jobs exactly once per slot.

    The manager owns only operating-task state transitions. It cannot call the
    research execution workflow, paper broker, order matcher, or stop-loss
    monitor. Business evidence and report content remain owned by
    ``DailyInvestmentOSService.generate_report``.
    """

    def __init__(
        self,
        service: OperatingReportService,
        report_center: InvestmentReportCenter | None = None,
        registry: TaskRegistry | None = None,
        now_provider: Callable[[], datetime] | None = None,
        trading_day_provider: Callable[[date], bool] | None = None,
        observability_jobs: Any | None = None,
    ) -> None:
        """Bind an operating service to its durable audit and schedule registry."""
        self.service = service
        self.report_center = (
            report_center
            or getattr(service, "report_center", None)
            or getattr(service, "center", None)
        )
        if self.report_center is None:
            raise ValueError("JobManager需要InvestmentReportCenter")
        self.registry = registry or getattr(service, "registry", None) or TaskRegistry()
        self._now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self._trading_day_provider = trading_day_provider or (
            lambda selected: selected.weekday() < 5
        )
        # Optional, fail-open telemetry sink. It records the scheduler outcome
        # but never owns report generation, retries or task state.
        self._observability_jobs = observability_jobs

    def run(
        self,
        job_name: str,
        scheduled_for: datetime | str | None = None,
        trigger: str = "scheduler",
        force: bool = False,
    ) -> dict[str, Any]:
        """Run one durable slot and never retry a claimed slot automatically.

        ``force`` is deliberately not allowed to defeat the durable idempotency
        key. A manual caller that needs a new attempt must use ``user_action``
        with a new ``scheduled_for`` timestamp; a scheduler occurrence always
        remains exactly-once.
        """
        definition = self.registry.get(job_name)
        slot = self._normalize_datetime(scheduled_for or self._now())
        if scheduled_for is None:
            # Browser/CLI retries in the same minute represent one business action.
            slot = slot.replace(second=0, microsecond=0)
        scheduled_iso = slot.isoformat()
        task, created = self._start_task_safely(
            job_name=definition.job_name,
            trade_date=slot.date().isoformat(),
            scheduled_for=scheduled_iso,
            trigger=trigger,
        )
        if not created:
            return self._result(
                state="duplicate",
                task=task,
                report=self._task_report(task),
                force_requested=force,
            )

        observed_job = self._begin_observation(
            definition.job_name,
            scheduled_iso,
            trigger=trigger,
            task_id=str(task.get("task_id") or ""),
        )

        if trigger == "scheduler" and not self._is_trading_day(slot.date()):
            task = self.report_center.finish_task(
                task["task_id"],
                status="skipped",
                error_message="非交易日，投资运营任务已安全跳过",
            )
            self._finish_observation(
                observed_job, "BLOCKED", evidence={"reason": "non_trading_day"}
            )
            return self._result(
                state="skipped",
                task=task,
                report=None,
                force_requested=force,
            )

        try:
            generated = dict(
                self.service.generate_report(
                    definition.report_type.value,
                    trade_date=slot.date().isoformat(),
                    scheduled_for=scheduled_iso,
                )
            )
            if self._is_skipped(generated):
                reason = self._skip_reason(generated)
                task = self.report_center.finish_task(
                    task["task_id"], status="skipped", error_message=reason
                )
                self._finish_observation(
                    observed_job, "BLOCKED", evidence={"reason": reason}
                )
                return self._result(
                    state="skipped",
                    task=task,
                    report=None,
                    force_requested=force,
                )

            report = self._extract_report(generated)
            report_id = str(report.get("report_id") or "")
            if not report_id:
                raise RuntimeError("Daily Investment OS报告缺少report_id")
            task = self.report_center.finish_task(
                task["task_id"], status="succeeded", report_id=report_id
            )
            self._finish_observation(
                observed_job,
                "SUCCEEDED",
                evidence={"task_id": task.get("task_id"), "report_id": report_id},
            )
            return self._result(
                state="succeeded",
                task=task,
                report=report,
                force_requested=force,
            )
        except Exception as exc:
            safe_error = self._safe_error(exc)
            self._finish_observation(
                observed_job,
                "FAILED",
                error_code=type(exc).__name__,
                error_message=safe_error,
            )
            try:
                self.report_center.finish_task(
                    task["task_id"], status="failed", error_message=safe_error
                )
            except InvestmentReportCenterError:
                # Preserve the original business failure if audit finalization
                # itself is unavailable. No automatic retry is started here.
                pass
            raise

    def _begin_observation(
        self,
        job_name: str,
        scheduled_for: str,
        *,
        trigger: str,
        task_id: str,
    ) -> str | None:
        """Start optional telemetry without making it a scheduler dependency."""
        if self._observability_jobs is None:
            return None
        try:
            job = self._observability_jobs.create(
                f"daily_os.{job_name}",
                f"daily-os:{job_name}:{scheduled_for}",
                scheduled_for=scheduled_for,
                evidence={"trigger": trigger, "operating_task_id": task_id},
            )
            if str(job.get("status")) == "CREATED":
                job = self._observability_jobs.transition(job["job_id"], "RUNNING")
            return str(job["job_id"])
        except Exception:
            return None

    def _finish_observation(
        self,
        job_id: str | None,
        status: str,
        *,
        evidence: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Finish optional telemetry; failure never changes the operating job."""
        if self._observability_jobs is None or not job_id:
            return
        try:
            self._observability_jobs.transition(
                job_id,
                status,
                evidence=dict(evidence or {}),
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            return

    def run_due(self, now: datetime | None = None) -> dict[str, Any]:
        """Run each due registry slot serially and keep failures isolated."""
        current = self._normalize_datetime(now or self._now())
        results: list[dict[str, Any]] = []
        for definition, slot in self.registry.due_jobs(current):
            try:
                results.append(
                    self.run(
                        definition.job_name,
                        scheduled_for=slot,
                        trigger="scheduler",
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "state": "failed",
                        "job_name": definition.job_name,
                        "scheduled_for": slot.isoformat(),
                        "error": self._safe_error(exc),
                        "can_trade": False,
                        "can_create_orders": False,
                    }
                )
        return {
            "version": JOB_MANAGER_VERSION,
            "observed_at": current.isoformat(),
            "jobs": results,
            "can_trade": False,
            "can_create_orders": False,
        }

    def _start_task_safely(self, **payload: Any) -> tuple[dict[str, Any], bool]:
        """Resolve a rare concurrent insert race through the store's unique key."""
        try:
            return self.report_center.start_task(**payload)
        except sqlite3.IntegrityError:
            # Another local process may have committed the same deterministic
            # slot between the store's SELECT and INSERT. Re-reading returns the
            # winner and prevents a second report/model call.
            return self.report_center.start_task(**payload)

    def _task_report(self, task: Mapping[str, Any]) -> dict[str, Any] | None:
        """Load an existing slot's report when it reached a successful terminal state."""
        report_id = str(task.get("report_id") or "")
        if not report_id:
            return None
        try:
            return self.report_center.report(report_id)
        except InvestmentReportCenterError:
            return None

    @staticmethod
    def _extract_report(generated: Mapping[str, Any]) -> dict[str, Any]:
        """Accept the service's saved-report payload or an explicit report envelope."""
        nested = generated.get("report")
        if isinstance(nested, Mapping):
            return dict(nested)
        return dict(generated)

    @staticmethod
    def _is_skipped(generated: Mapping[str, Any]) -> bool:
        """Recognize a service-owned fail-closed skip without inventing a report."""
        return str(generated.get("state") or generated.get("status") or "").lower() in {
            "skipped",
            "not_due",
            "not_trading_day",
        }

    @staticmethod
    def _skip_reason(generated: Mapping[str, Any]) -> str:
        """Return a bounded skip reason suitable for durable audit."""
        return str(
            generated.get("message")
            or generated.get("reason")
            or "任务依赖未满足，已安全跳过"
        )[:1000]

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Redact provider secrets before persisting or returning scheduler errors."""
        message = str(exc).strip()[:800]
        if not message or _SECRET_PATTERN.search(message):
            message = "错误详情因可能含敏感信息已隐藏"
        return f"{type(exc).__name__}: {message}"

    def _now(self) -> datetime:
        """Read one timezone-aware scheduler clock value."""
        return self._normalize_datetime(self._now_provider())

    def _is_trading_day(self, selected: date) -> bool:
        """Fail closed when an injected official-calendar check is unavailable."""
        try:
            return bool(self._trading_day_provider(selected))
        except Exception:
            return False

    @staticmethod
    def _normalize_datetime(value: datetime | str) -> datetime:
        """Normalize ISO or datetime input to the Asia/Shanghai operating clock."""
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=SHANGHAI_TZ)
        return parsed.astimezone(SHANGHAI_TZ)

    @staticmethod
    def _result(
        *,
        state: str,
        task: Mapping[str, Any],
        report: Mapping[str, Any] | None,
        force_requested: bool,
    ) -> dict[str, Any]:
        """Shape a stable non-executable job result for CLI, API, and tests."""
        return {
            "version": JOB_MANAGER_VERSION,
            "state": state,
            "job_name": task.get("job_name"),
            "scheduled_for": task.get("scheduled_for"),
            "force_requested": bool(force_requested),
            "task": dict(task),
            "report": dict(report) if report is not None else None,
            "can_trade": False,
            "can_create_orders": False,
        }
