from __future__ import annotations

from argparse import ArgumentParser
import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_agent.scheduler import DailyScheduler, JobManager
from ashare_agent.scheduler.task_registry import TaskRegistry


JOB_NAMES = tuple(TaskRegistry().jobs)


def _parser() -> ArgumentParser:
    """Build the non-trading Daily Investment OS command contract."""
    parser = ArgumentParser(description="盘古·天机 Pangu V2 投资运营任务")
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run", help="运行一个到期的投资运营报告任务")
    run.add_argument("job_name", choices=JOB_NAMES)
    run.add_argument(
        "--force",
        action="store_true",
        help="以用户显式动作立即运行；仍不创建订单且不覆盖同一任务槽",
    )
    commands.add_parser("due", help="运行当前全部到期任务")
    commands.add_parser("status", help="只读显示调度配置和当前到期任务")
    return parser


def _scheduler():
    """Construct services lazily so imports cannot start background work."""
    from ashare_agent.services.daily_investment_os_service import (
        DailyInvestmentOSService,
    )
    from pangu.config import ConfigCenter
    from pangu.observability.service import ObservabilityService

    service = DailyInvestmentOSService(PROJECT_ROOT)
    daily = getattr(service, "daily", None)
    trading_days = getattr(daily, "_trading_days", None)
    official_calendar = (
        (lambda selected: selected.isoformat() in trading_days())
        if callable(trading_days)
        else None
    )
    config = ConfigCenter(PROJECT_ROOT).load()
    observability = ObservabilityService(
        PROJECT_ROOT,
        dict(config.configuration["observability"]),
        config.config_hash,
    )
    manager = JobManager(
        service,
        report_center=getattr(service, "report_center", None),
        registry=getattr(service, "registry", None),
        trading_day_provider=official_calendar,
        observability_jobs=observability.jobs,
    )
    return service, DailyScheduler(manager)


def _safe_failure(exc: Exception) -> dict[str, Any]:
    """Return a non-secret CLI failure while detailed audit stays in the database."""
    return {
        "state": "failed",
        "error_type": type(exc).__name__,
        "message": "投资运营任务失败，请在盘古·天机任务审计中查看脱敏原因",
        "can_trade": False,
        "can_create_orders": False,
    }


def main(argv: list[str] | None = None) -> int:
    """Execute one explicit scheduler tick and return a Task Scheduler exit code."""
    args = _parser().parse_args(argv)
    service = None
    try:
        service, scheduler = _scheduler()
        if args.action == "status":
            result = scheduler.status()
        elif args.action == "due":
            result = scheduler.tick()
        else:
            result = scheduler.run_job(
                args.job_name,
                trigger="user_action" if args.force else "scheduler",
                force=bool(args.force),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("state") == "failed":
            return 1
        if any(item.get("state") == "failed" for item in result.get("jobs", [])):
            return 1
        return 0
    except Exception as exc:
        print(json.dumps(_safe_failure(exc), ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        if service is not None:
            close = getattr(service, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    raise SystemExit(main())
