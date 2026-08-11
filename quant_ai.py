from __future__ import annotations

from argparse import ArgumentParser
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_agent.quant_ai import AIQuantResearchService, ResearchAnalystScheduler


def parser() -> ArgumentParser:
    """Build a research-only command surface for Windows Task Scheduler."""
    value = ArgumentParser(description="盘古·天机 AI量化研究员")
    commands = value.add_subparsers(dest="action", required=True)
    commands.add_parser("due", help="仅在当前08:00任务槽到期时生成研究晨报")
    commands.add_parser("run", help="通过用户显式动作立即生成研究晨报")
    commands.add_parser("status", help="只读显示AI研究员状态与调度配置")
    return value


def main(argv: list[str] | None = None) -> int:
    """Run one bounded research action and return a scheduler-compatible exit code."""
    args = parser().parse_args(argv)
    try:
        service = AIQuantResearchService(PROJECT_ROOT)
        scheduler = ResearchAnalystScheduler(service)
        if args.action == "due":
            result = scheduler.tick()
        elif args.action == "run":
            result = scheduler.run_now()
        else:
            result = {
                "service": service.dashboard(),
                "scheduler": scheduler.status(),
                "can_trade": False,
                "can_create_orders": False,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("state") == "failed" else 0
    except Exception as exc:
        print(json.dumps({
            "state": "failed",
            "error_type": type(exc).__name__,
            "message": "AI量化研究任务失败，请在研究中心查看脱敏审计",
            "can_trade": False,
            "can_create_orders": False,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
