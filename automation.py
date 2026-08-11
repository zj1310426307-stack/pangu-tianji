from argparse import ArgumentParser
from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ashare_agent.services.daily_research_service import DailyResearchService


def main() -> None:
    parser = ArgumentParser(description="盘古·天机 v0.9 自动任务")
    parser.add_argument("action", choices=("collect", "execute", "monitor", "status"))
    args = parser.parse_args()
    service = DailyResearchService(ROOT)
    try:
        result = service.dashboard() if args.action == "status" else getattr(service, args.action)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        service.close()


if __name__ == "__main__":
    main()
