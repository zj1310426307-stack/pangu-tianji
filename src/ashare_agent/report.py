from pathlib import Path
import json
import pandas as pd


def write_report(metrics: dict, equity_curve: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with (output_dir / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    equity_curve.to_csv(output_dir / "equity_curve.csv", index=False)

    report = f"""# 盘古·天机回测报告

> 数据说明：本报告使用程序生成的合成行情，仅用于验证系统流程，不能用于评价真实策略收益。

## 核心指标

| 指标 | 结果 |
|---|---:|
| 初始权益 | {metrics['initial_equity']:.2f}元 |
| 期末权益 | {metrics['final_equity']:.2f}元 |
| 总收益率 | {metrics['total_return_pct']:.2f}% |
| 年化收益率 | {metrics['annual_return_pct']:.2f}% |
| 年化波动率 | {metrics['annual_volatility_pct']:.2f}% |
| 最大回撤 | {metrics['max_drawdown_pct']:.2f}% |
| 夏普比率 | {metrics['sharpe']:.3f} |
| 成交次数 | {metrics['trade_count']} |
| 拒绝订单数 | {metrics['rejected_order_count']} |

## 运行边界

- 仅回测和模拟交易。
- 未连接真实证券账户。
- 未调用普通同花顺交易界面。
- 未启用实盘下单。
- 回测结果不构成投资建议。
"""
    (output_dir / "backtest_report.md").write_text(report, encoding="utf-8")
