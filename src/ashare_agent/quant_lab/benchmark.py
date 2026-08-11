from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import pandas as pd

from .contracts import sha256_json
from .exceptions import ContractError


@dataclass(frozen=True)
class BenchmarkContract:
    """Identify an honest, versioned benchmark source and construction method."""

    benchmark_id: str
    version: str
    kind: str
    source: str

    def validate(self) -> None:
        """Allow only cash, universe equal-weight or explicitly supplied real indexes."""
        if self.kind not in {"cash", "equal_weight_universe", "real_index"}:
            raise ContractError("不支持的benchmark类型")
        if not all((self.benchmark_id, self.version, self.source)):
            raise ContractError("benchmark合同字段不得为空")


@dataclass(frozen=True)
class BenchmarkResult:
    """Return a benchmark series or an explicit unavailable state."""

    contract: BenchmarkContract
    availability: str
    returns: tuple[tuple[str, float], ...]
    reason: str | None = None

    @property
    def benchmark_hash(self) -> str:
        """Hash benchmark values and provenance."""
        return sha256_json(self)


class BenchmarkProvider:
    """Build deterministic baselines without inventing an index history."""

    def cash(self, dates: Sequence[str], contract: BenchmarkContract) -> BenchmarkResult:
        """Return the zero-return cash benchmark for supplied evaluation dates."""
        contract.validate()
        if contract.kind != "cash":
            raise ContractError("cash方法需要kind=cash")
        return BenchmarkResult(contract, "available", tuple((str(day), 0.0) for day in dates))

    def equal_weight_universe(
        self, returns: pd.DataFrame, contract: BenchmarkContract
    ) -> BenchmarkResult:
        """Compute a date-level equal-weight return from point-in-time universe members."""
        contract.validate()
        if contract.kind != "equal_weight_universe":
            raise ContractError("equal_weight_universe方法合同类型不一致")
        if not {"date", "symbol", "return"}.issubset(returns.columns):
            raise ContractError("等权基准需要date、symbol、return")
        frame = returns.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["return"] = pd.to_numeric(frame["return"], errors="coerce")
        if frame[["date", "symbol", "return"]].isna().any().any():
            raise ContractError("等权基准包含无效数据")
        values = frame.groupby("date", sort=True)["return"].mean()
        return BenchmarkResult(
            contract, "available",
            tuple((index.date().isoformat(), float(value)) for index, value in values.items()),
        )

    def real_index(
        self,
        contract: BenchmarkContract,
        loader: Callable[[], pd.DataFrame] | None,
    ) -> BenchmarkResult:
        """Use only a supplied real index series; otherwise report unavailable."""
        contract.validate()
        if contract.kind != "real_index":
            raise ContractError("real_index方法合同类型不一致")
        if loader is None:
            return BenchmarkResult(contract, "unavailable", (), "未提供真实指数点时数据")
        frame = loader()
        if not {"date", "return"}.issubset(frame.columns):
            return BenchmarkResult(contract, "unavailable", (), "真实指数缺少date或return")
        frame = frame[["date", "return"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["return"] = pd.to_numeric(frame["return"], errors="coerce")
        if frame.isna().any().any() or frame.empty:
            return BenchmarkResult(contract, "unavailable", (), "真实指数数据无效或为空")
        return BenchmarkResult(
            contract, "available",
            tuple((row.date.date().isoformat(), float(row.return_value)) for row in frame.rename(
                columns={"return": "return_value"}
            ).itertuples(index=False)),
        )
