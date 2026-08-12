"""Local CLI for Pangu V3.1 engineering health and verified backups."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(ROOT / "src"))

from pangu.engineering import EngineeringService  # noqa: E402
from ashare_agent.services.model_service import ModelService  # noqa: E402


def main() -> int:
    """Run one explicit engineering operation with no investment side effects."""
    parser = argparse.ArgumentParser(description="盘古·天机 V3.1 工程运维")
    parser.add_argument(
        "command",
        choices=(
            "status", "health", "backup", "observability", "observe", "alerts",
            "incidents", "trace", "retention-plan", "ack-alert", "incident-status",
            "retention-run",
        ),
    )
    parser.add_argument("--trace-id", default=None)
    parser.add_argument("--alert-id", default=None)
    parser.add_argument("--incident-id", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--expected-version", type=int, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument("--actor", default="local-user")
    parser.add_argument("--retention-id", default=None)
    parser.add_argument("--plan-hash", default=None)
    args = parser.parse_args()
    model_service = ModelService()
    service = EngineeringService(ROOT, model_status_provider=model_service.status)
    if args.command == "status":
        result = service.dashboard()
    elif args.command == "health":
        result = service.run_health()
    elif args.command == "backup":
        result = service.create_backup()
    elif args.command == "observability":
        result = service.observability.dashboard()
    elif args.command == "observe":
        result = service.observability.evaluate()
    elif args.command == "alerts":
        result = service.observability.list_alerts(limit=100)
    elif args.command == "incidents":
        result = service.observability.list_incidents(limit=100)
    elif args.command == "trace":
        if not args.trace_id:
            parser.error("trace 需要 --trace-id")
        result = service.observability.traces.trace(args.trace_id)
        if result is None:
            parser.error("Trace 不存在")
    elif args.command == "retention-plan":
        result = service.observability.retention.plan()
    elif args.command == "ack-alert":
        if not args.alert_id:
            parser.error("ack-alert 需要 --alert-id")
        result = service.observability.alerts.acknowledge(args.alert_id, actor=args.actor, note=args.note)
    elif args.command == "incident-status":
        if not args.incident_id or not args.status or args.expected_version is None:
            parser.error("incident-status 需要 --incident-id、--status 和 --expected-version")
        result = service.observability.incidents.transition(
            args.incident_id, target=args.status, expected_version=args.expected_version,
            actor=args.actor, note=args.note or "CLI手工维护",
        )
    else:
        if not args.retention_id or not args.plan_hash:
            parser.error("retention-run 需要 --retention-id 和 --plan-hash")
        result = service.observability.retention.run(args.retention_id, args.plan_hash)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
