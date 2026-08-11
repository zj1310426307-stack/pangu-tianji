from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd


DATA_MODEL_VERSION = "pangu-data-model-v2.0.0"


class DataModelError(ValueError):
    """Reject an asset that does not satisfy the shared investment-data contract."""


@dataclass(frozen=True)
class TabularDataModel:
    """Describe one versioned tabular asset and its point-in-time constraints."""

    name: str
    required_columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    time_column: str | None = None

    def validate(self, frame: pd.DataFrame, *, as_of: pd.Timestamp) -> dict[str, Any]:
        """Validate schema, uniqueness and future-data leakage for one frame."""
        missing = sorted(set(self.required_columns) - set(frame.columns))
        if missing:
            raise DataModelError(f"{self.name}缺少字段：{missing}")
        if self.primary_key and frame.duplicated(list(self.primary_key)).any():
            raise DataModelError(f"{self.name}主键重复：{self.primary_key}")
        future_rows = 0
        if self.time_column and self.time_column in frame:
            values = pd.to_datetime(frame[self.time_column], errors="coerce").dt.normalize()
            if values.isna().any():
                raise DataModelError(f"{self.name}.{self.time_column}包含无效时间")
            future_rows = int((values > pd.Timestamp(as_of).normalize()).sum())
            if future_rows:
                raise DataModelError(f"{self.name}包含{future_rows}条未来数据")
        return {
            "model": self.name,
            "model_version": DATA_MODEL_VERSION,
            "rows": int(len(frame)),
            "primary_key": list(self.primary_key),
            "time_column": self.time_column,
            "future_rows": future_rows,
            "valid": True,
        }


SECURITY_MASTER = TabularDataModel(
    name="Security Master",
    required_columns=("symbol", "name"),
    primary_key=("symbol",),
)

MARKET_DATA_SNAPSHOT = TabularDataModel(
    name="Market Data",
    required_columns=("symbol", "last_price", "turnover", "volume"),
    primary_key=("symbol",),
)

MARKET_DATA_HISTORY = TabularDataModel(
    name="Market Data",
    required_columns=("date", "symbol", "close", "turnover"),
    primary_key=("date", "symbol"),
    time_column="date",
)

FINANCIAL_POINT_IN_TIME = TabularDataModel(
    name="Financial Point-In-Time",
    required_columns=("symbol", "available_at"),
    primary_key=("symbol", "available_at"),
    time_column="available_at",
)

FEATURE_STORE = TabularDataModel(
    name="Feature Store",
    required_columns=("symbol", "momentum", "volatility", "max_drawdown"),
    primary_key=("symbol",),
)


@dataclass(frozen=True)
class ResearchSnapshotModel:
    """Validate the non-tabular evidence contract for one completed research run."""

    name: str = "Research Snapshot"

    def validate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Require the complete strategy, factor, ranking and portfolio lineage."""
        required = {
            "run_id",
            "research_date",
            "strategy_version",
            "factor_version",
            "factor_contract_hash",
            "data_version",
            "ranking_count",
            "targets",
            "target_weights",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise DataModelError(f"{self.name}缺少字段：{missing}")
        return {
            "model": self.name,
            "model_version": DATA_MODEL_VERSION,
            "valid": True,
        }


RESEARCH_SNAPSHOT = ResearchSnapshotModel()
