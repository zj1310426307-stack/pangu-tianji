from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_agent.agent import TradingResearchAgent
from ashare_agent.core.project_lock import ProjectRunLock


if __name__ == "__main__":
    lock = ProjectRunLock(PROJECT_ROOT / "output" / ".paper_run.lock")
    if not lock.acquire():
        raise SystemExit("另一个本地进程正在运行本项目，请稍后再试。")
    try:
        agent = TradingResearchAgent(PROJECT_ROOT / "config" / "settings.yaml")
        result = agent.run()
    finally:
        lock.release()
    print("\n运行完成")
    print(f"总收益率: {result['total_return_pct']:.2f}%")
    print(f"最大回撤: {result['max_drawdown_pct']:.2f}%")
    print(f"成交次数: {result['trade_count']}")
    print("报告: output/backtest_report.md")
