"""Run the production-equivalent point-in-time stock backtest.

The command intentionally refuses to substitute the legacy ETF files. Three
historical long-form datasets are required under ``data/cross_sectional``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ashare_agent.cross_sectional_backtest import (  # noqa: E402
    CrossSectionalBacktestEngine,
    point_in_time_dataset_status,
)
from ashare_agent.data_center import DataCenter  # noqa: E402


def _read(name: str) -> pd.DataFrame:
    """Read one immutable Parquet input with a focused dependency message."""
    path = ROOT / "data" / "cross_sectional" / f"{name}.parquet"
    try:
        return pd.read_parquet(path)
    except ImportError as exc:
        raise RuntimeError("读取Parquet需要pyarrow，请重新执行pip install -r requirements.txt") from exc


def main() -> None:
    """Validate inputs, run the shared factor model and write audit artifacts."""
    status = point_in_time_dataset_status(ROOT)
    if not status["ready"]:
        raise SystemExit(f"生产同构回测未运行：{status['message']}；缺少 {', '.join(status['missing'])}")
    benchmark_path = ROOT / "data" / "cross_sectional" / "benchmark.parquet"
    benchmark = pd.read_parquet(benchmark_path) if benchmark_path.exists() else None
    result = CrossSectionalBacktestEngine(
        _read("bars"),
        _read("fundamentals"),
        _read("universe"),
        benchmark,
        data_center=DataCenter(ROOT / "output" / "data_center"),
    ).run()
    output = ROOT / "output" / "cross_sectional_backtest"
    output.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(output / "equity_curve.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    result.rankings.to_csv(output / "rankings.csv", index=False)
    (output / "metrics.json").write_text(
        json.dumps(result.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
