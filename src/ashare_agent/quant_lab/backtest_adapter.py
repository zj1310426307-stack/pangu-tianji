from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

import pandas as pd

from .contracts import sha256_json
from .metrics import MetricMetadata, calculate_metrics


class ExistingBacktestEngine(Protocol):
    """Describe the existing CrossSectionalBacktestEngine surface without duplicating it."""

    research_records: list[Any]

    def run(self) -> Any:
        """Run the existing production-equivalent historical engine."""


@dataclass(frozen=True)
class BacktestAdapterResult:
    """Normalize existing engine evidence for experiment artifacts."""

    summary: dict[str, Any]
    data_center_run_ids: tuple[str, ...]
    data_versions: tuple[str, ...]
    equity_records: tuple[dict[str, Any], ...]
    trade_records: tuple[dict[str, Any], ...]
    ranking_records: tuple[dict[str, Any], ...]
    adapter_version: str = "existing-cross-sectional-adapter-v1.0.0"
    used_research_pipeline: bool = True
    can_trade: bool = False
    can_create_orders: bool = False

    @property
    def result_hash(self) -> str:
        """Hash adapter evidence without mutating the historical engine."""
        return sha256_json(asdict(self))


class ExistingBacktestAdapter:
    """Invoke the existing engine and add experiment-level lineage and normalized metrics."""

    def run(
        self,
        engine: ExistingBacktestEngine,
        *,
        benchmark_returns: pd.Series | None = None,
    ) -> BacktestAdapterResult:
        """Run an existing engine and preserve every Data Center run identity it used."""
        result = engine.run()
        equity = result.equity_curve.copy()
        trades = result.trades.copy()
        rankings = result.rankings.copy()
        metrics = calculate_metrics(
            equity,
            trades,
            benchmark_returns,
            metadata=MetricMetadata(frequency="daily", periods_per_year=252),
        )
        metrics["existing_engine_metrics"] = dict(result.metrics)
        records = tuple(getattr(engine, "research_records", ()))
        return BacktestAdapterResult(
            summary=metrics,
            data_center_run_ids=tuple(str(item.run_id) for item in records),
            data_versions=tuple(str(item.data_version) for item in records),
            equity_records=tuple(self._records(equity)),
            trade_records=tuple(self._records(trades)),
            ranking_records=tuple(self._records(rankings)),
        )

    @staticmethod
    def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        """Normalize pandas timestamps for canonical JSON artifacts."""
        records = frame.to_dict("records")
        for row in records:
            for key, value in list(row.items()):
                if isinstance(value, pd.Timestamp):
                    row[key] = value.isoformat()
        return records
