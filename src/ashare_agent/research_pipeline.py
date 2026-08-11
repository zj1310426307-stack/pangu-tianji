from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Protocol

import numpy as np
import pandas as pd

from .factor_model import FACTOR_CONTRACT_HASH, FACTOR_MODEL_VERSION, score_cross_section


STRATEGY_VERSION = "cross-sectional-v2.0.0"
MAINBOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
CORE_FINANCIAL_FIELDS = ("profit_growth", "revenue_growth", "roe", "cash_quality")


class ResearchPipelineError(RuntimeError):
    """Reject an incomplete research run before it can publish a ranking."""


class ResearchDataSource(Protocol):
    """Provide point-in-time inputs while keeping provider I/O outside strategy logic."""

    def security_universe(self, as_of: pd.Timestamp) -> pd.DataFrame:
        """Return security master rows visible on the research date."""

    def data_snapshot(self, as_of: pd.Timestamp, universe: pd.DataFrame) -> pd.DataFrame:
        """Return one normalized market snapshot for the supplied universe."""

    def price_history(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return normalized daily history ending on the research date."""

    def fundamentals(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return point-in-time financial rows for the supplied symbols."""


@dataclass(frozen=True)
class ResearchPipelineConfig:
    """Version the complete production research contract independently of data providers."""

    minimum_turnover: float = 50_000_000.0
    minimum_price: float = 2.0
    maximum_price: float = 200.0
    minimum_history_rows: int = 250
    prefilter_count: int = 120
    financial_count: int = 60
    recommendation_count: int = 30
    target_count: int = 5
    entry_rank: int = 8
    max_positions_per_industry: int = 2
    max_pairwise_correlation: float = 0.85
    max_single_position_pct: float = 0.15
    target_total_exposure_pct: float = 0.60

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ResearchPipelineConfig":
        """Build the shared contract from the validated application configuration."""
        return cls(
            minimum_turnover=float(values.get("minimum_turnover", cls.minimum_turnover)),
            minimum_price=float(values.get("minimum_price", cls.minimum_price)),
            maximum_price=float(values.get("maximum_price", cls.maximum_price)),
            minimum_history_rows=int(values.get("minimum_history_rows", cls.minimum_history_rows)),
            prefilter_count=int(values.get("prefilter_count", cls.prefilter_count)),
            financial_count=int(values.get("financial_count", cls.financial_count)),
            recommendation_count=int(values.get("recommendation_count", cls.recommendation_count)),
            target_count=int(values.get("target_count", cls.target_count)),
            entry_rank=int(values.get("entry_rank", cls.entry_rank)),
            max_positions_per_industry=int(
                values.get("max_positions_per_industry", cls.max_positions_per_industry)
            ),
            max_pairwise_correlation=float(
                values.get("max_pairwise_correlation", cls.max_pairwise_correlation)
            ),
            max_single_position_pct=float(
                values.get("max_single_position_pct", cls.max_single_position_pct)
            ),
            target_total_exposure_pct=float(
                values.get("target_total_exposure_pct", cls.target_total_exposure_pct)
            ),
        )

    def validate(self) -> None:
        """Reject internal strategy drift before a data source is queried."""
        if self.minimum_turnover < 0 or not 0 < self.minimum_price < self.maximum_price:
            raise ValueError("Research Pipeline价格或流动性配置无效")
        if self.minimum_history_rows < 121:
            raise ValueError("Research Pipeline至少需要121行历史数据")
        if not self.target_count <= self.entry_rank <= self.recommendation_count:
            raise ValueError("Research Pipeline目标数、入场排名和推荐数不一致")
        if not self.recommendation_count <= self.financial_count <= self.prefilter_count:
            raise ValueError("Research Pipeline预筛漏斗数量不一致")
        if self.max_positions_per_industry < 1:
            raise ValueError("Research Pipeline行业持仓上限必须为正整数")
        if not 0 < self.max_pairwise_correlation <= 1.01:
            raise ValueError("Research Pipeline相关性阈值无效")
        if not 0 < self.max_single_position_pct <= self.target_total_exposure_pct <= 1:
            raise ValueError("Research Pipeline目标仓位配置无效")


@dataclass
class ResearchPipelineResult:
    """Expose every deterministic research stage for audit and downstream presentation."""

    strategy_version: str
    factor_model_version: str
    factor_contract_hash: str
    as_of: pd.Timestamp
    raw_universe: pd.DataFrame
    raw_snapshot: pd.DataFrame
    raw_history: pd.DataFrame
    raw_fundamentals: pd.DataFrame
    universe: pd.DataFrame
    snapshot: pd.DataFrame
    features: pd.DataFrame
    factor_results: pd.DataFrame
    ranked: pd.DataFrame
    targets: list[str]
    target_weights: dict[str, float]
    portfolio_rejections: dict[str, str]
    return_history: dict[str, pd.Series]
    stage_counts: dict[str, int]
    data_quality: dict[str, Any]


def _percentile(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Convert one comparable metric into a deterministic zero-to-one rank."""
    ranked = series.rank(method="average", pct=True)
    return ranked if higher_is_better else 1.0 - ranked + (1.0 / max(len(series), 1))


class ResearchPipeline:
    """Own the one strategy path used by live research and historical backtests.

    Provider adapters are responsible only for retrieving point-in-time inputs.
    This class exclusively owns the ordered strategy stages: Security Universe,
    Data Snapshot, Feature Calculation, Factor Score, Ranking and Portfolio
    Construction.
    """

    def __init__(self, config: ResearchPipelineConfig | None = None) -> None:
        self.config = config or ResearchPipelineConfig()
        self.config.validate()

    def run(self, as_of: pd.Timestamp | str, source: ResearchDataSource) -> ResearchPipelineResult:
        """Execute all research stages through a provider-neutral contract."""
        signal_date = pd.Timestamp(as_of).normalize()
        raw_universe = source.security_universe(signal_date)
        raw_snapshot = source.data_snapshot(signal_date, raw_universe)
        universe, snapshot = self.security_universe(signal_date, raw_universe, raw_snapshot)
        if len(snapshot) < self.config.target_count:
            raise ResearchPipelineError("通过证券池与行情校验的股票不足，禁止生成研究结果")

        prefiltered = snapshot.head(self.config.prefilter_count).copy()
        bars = source.price_history(signal_date, prefiltered["symbol"].astype(str).tolist())
        features, return_history = self.feature_calculation(signal_date, prefiltered, bars)
        if len(features) < self.config.target_count:
            raise ResearchPipelineError("通过历史数据校验的股票不足，禁止生成研究结果")

        features = features.copy()
        features["technical_prefilter"] = (
            _percentile(features["momentum"]) * 0.45
            + _percentile(features["volatility"], False) * 0.20
            + _percentile(features["max_drawdown"]) * 0.20
            + _percentile(features["median_turnover_20d"]) * 0.15
        )
        financial_universe = features.sort_values(
            ["technical_prefilter", "symbol"], ascending=[False, True]
        ).head(self.config.financial_count)
        fundamentals = source.fundamentals(
            signal_date, financial_universe["symbol"].astype(str).tolist()
        )
        scored_input = self.data_snapshot_with_fundamentals(
            signal_date, financial_universe, fundamentals
        )
        if len(scored_input) < self.config.target_count:
            raise ResearchPipelineError("财务指标完整的股票不足，禁止生成研究结果")

        scored = self.factor_score(scored_input)
        ranked = self.ranking(scored)
        targets, target_weights, portfolio_rejections = self.portfolio_construction(
            ranked, return_history
        )
        valuation_coverage = (
            float(ranked["valuation_complete"].astype(bool).mean()) if len(ranked) else 0.0
        )
        return ResearchPipelineResult(
            strategy_version=STRATEGY_VERSION,
            factor_model_version=FACTOR_MODEL_VERSION,
            factor_contract_hash=FACTOR_CONTRACT_HASH,
            as_of=signal_date,
            raw_universe=raw_universe.copy(),
            raw_snapshot=raw_snapshot.copy(),
            raw_history=bars.copy(),
            raw_fundamentals=fundamentals.copy(),
            universe=universe,
            snapshot=snapshot,
            features=features,
            factor_results=scored,
            ranked=ranked,
            targets=targets,
            target_weights=target_weights,
            portfolio_rejections=portfolio_rejections,
            return_history=return_history,
            stage_counts={
                "security_universe": int(len(universe)),
                "data_snapshot": int(len(snapshot)),
                "feature_calculation": int(len(features)),
                "factor_score": int(len(scored)),
                "ranking": int(len(ranked)),
                "portfolio_construction": int(len(targets)),
            },
            data_quality={
                "industry_neutralization": bool(
                    ranked.get("industry", pd.Series(dtype=object))
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .any()
                ),
                "valuation_coverage": valuation_coverage,
                "financial_coverage": float(len(scored_input) / max(len(financial_universe), 1)),
            },
        )

    def security_universe(
        self,
        as_of: pd.Timestamp,
        universe: pd.DataFrame,
        snapshot: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Apply the shared board, status, listing, price and liquidity policy."""
        if "symbol" not in universe or "symbol" not in snapshot:
            raise ResearchPipelineError("证券池或行情快照缺少symbol")
        securities = universe.copy()
        securities["symbol"] = securities["symbol"].astype(str)
        securities = securities.drop_duplicates("symbol", keep="last")
        if "name" not in securities:
            securities["name"] = securities["symbol"]
        if "industry" not in securities:
            securities["industry"] = None
        codes = securities["symbol"].str.split(".", regex=False).str[0]
        board_ok = securities["symbol"].str.endswith((".SH", ".SZ")) & codes.str.startswith(
            MAINBOARD_PREFIXES
        )
        names = securities["name"].astype(str).str.upper()
        active = board_ok & ~names.str.contains("ST|退", regex=True)
        if "list_date" in securities:
            listed = pd.to_datetime(securities["list_date"], errors="coerce").dt.normalize()
            active &= listed.isna() | (listed <= as_of)
        if "delist_date" in securities:
            delisted = pd.to_datetime(securities["delist_date"], errors="coerce").dt.normalize()
            active &= delisted.isna() | (delisted > as_of)
        securities = securities.loc[active].copy()

        quotes = snapshot.copy()
        quotes["symbol"] = quotes["symbol"].astype(str)
        quotes = quotes.drop_duplicates("symbol", keep="last")
        merged = securities.merge(quotes, on="symbol", how="inner", suffixes=("", "_quote"))
        for column in ("last_price", "turnover", "volume"):
            merged[column] = pd.to_numeric(merged.get(column), errors="coerce")
        tradeable = merged.get("tradeable", pd.Series(True, index=merged.index)).fillna(False).astype(bool)
        valid = (
            np.isfinite(merged["last_price"])
            & np.isfinite(merged["turnover"])
            & np.isfinite(merged["volume"])
            & merged["last_price"].between(self.config.minimum_price, self.config.maximum_price)
            & (merged["turnover"] >= self.config.minimum_turnover)
            & (merged["volume"] > 0)
            & tradeable
        )
        eligible = merged.loc[valid].sort_values(
            ["turnover", "symbol"], ascending=[False, True]
        ).reset_index(drop=True)
        return securities.reset_index(drop=True), eligible

    def feature_calculation(
        self,
        as_of: pd.Timestamp,
        snapshot: pd.DataFrame,
        bars: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
        """Calculate the exact technical features and date-aligned return histories."""
        required = {"date", "symbol", "close", "turnover"}
        if not required.issubset(bars.columns):
            raise ResearchPipelineError(f"历史行情缺少字段：{sorted(required - set(bars.columns))}")
        history = bars.copy()
        history["date"] = pd.to_datetime(history["date"], errors="coerce").dt.normalize()
        history["symbol"] = history["symbol"].astype(str)
        for column in ("close", "turnover"):
            history[column] = pd.to_numeric(history[column], errors="coerce")
        history = history[
            history["date"].notna()
            & (history["date"] <= as_of)
            & np.isfinite(history["close"])
            & (history["close"] > 0)
        ].drop_duplicates(["date", "symbol"], keep="last")
        rows: list[dict[str, Any]] = []
        return_history: dict[str, pd.Series] = {}
        snapshot_by_symbol = snapshot.set_index("symbol", drop=False)
        for symbol in snapshot["symbol"].astype(str):
            selected = history[history["symbol"] == symbol].sort_values("date").tail(
                self.config.minimum_history_rows
            )
            if len(selected) < self.config.minimum_history_rows or selected["date"].iloc[-1] != as_of:
                continue
            close = selected.set_index("date")["close"].astype(float)
            turnover = selected.set_index("date")["turnover"].astype(float)
            if len(close) < 121:
                continue
            median_turnover = float(turnover.tail(20).median())
            if not math.isfinite(median_turnover) or median_turnover < self.config.minimum_turnover:
                continue
            returns = close.pct_change().dropna()
            ma20 = float(close.tail(20).mean())
            ma60 = float(close.tail(60).mean())
            row = snapshot_by_symbol.loc[symbol].to_dict()
            rows.append(
                {
                    **row,
                    "symbol": symbol,
                    "close": float(close.iloc[-1]),
                    "ma20": ma20,
                    "ma60": ma60,
                    "momentum": float(close.iloc[-21] / close.iloc[-121] - 1),
                    "volatility": float(returns.tail(60).std(ddof=0) * np.sqrt(252)),
                    "max_drawdown": float((close.tail(120) / close.tail(120).cummax() - 1).min()),
                    "median_turnover_20d": median_turnover,
                    "trend_strength": 1.0
                    if close.iloc[-1] > ma20 > ma60
                    else 0.5
                    if close.iloc[-1] > ma60
                    else 0.0,
                }
            )
            return_history[symbol] = returns.tail(120)
        return pd.DataFrame(rows), return_history

    def data_snapshot_with_fundamentals(
        self,
        as_of: pd.Timestamp,
        features: pd.DataFrame,
        fundamentals: pd.DataFrame,
    ) -> pd.DataFrame:
        """Join only financial rows visible at the signal time and enforce core coverage."""
        if fundamentals.empty or "symbol" not in fundamentals:
            return pd.DataFrame()
        financial = fundamentals.copy()
        financial["symbol"] = financial["symbol"].astype(str)
        if "available_at" in financial:
            financial["available_at"] = pd.to_datetime(
                financial["available_at"], errors="coerce"
            ).dt.normalize()
            financial = financial[
                financial["available_at"].notna() & (financial["available_at"] <= as_of)
            ]
            financial = financial.sort_values("available_at").groupby(
                "symbol", as_index=False
            ).tail(1)
        else:
            financial = financial.drop_duplicates("symbol", keep="last")
        merged = features.merge(financial, on="symbol", how="inner", suffixes=("", "_financial"))
        if not set(CORE_FINANCIAL_FIELDS).issubset(merged.columns):
            return pd.DataFrame()
        complete = merged[list(CORE_FINANCIAL_FIELDS)].apply(
            pd.to_numeric, errors="coerce"
        ).notna().all(axis=1)
        return merged.loc[complete].reset_index(drop=True)

    def factor_score(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Delegate the retained factor contract without duplicating factor mathematics."""
        return score_cross_section(frame)

    def ranking(self, scored: pd.DataFrame) -> pd.DataFrame:
        """Publish one deterministic ranked list shared by all execution environments."""
        ranked = scored.sort_values(["score", "symbol"], ascending=[False, True]).head(
            self.config.recommendation_count
        ).reset_index(drop=True)
        ranked["rank"] = np.arange(1, len(ranked) + 1)
        return ranked

    def portfolio_construction(
        self,
        ranked: pd.DataFrame,
        return_history: dict[str, pd.Series],
    ) -> tuple[list[str], dict[str, float], dict[str, str]]:
        """Apply shared entry, industry, correlation and inverse-volatility rules."""
        targets: list[str] = []
        rejections: dict[str, str] = {}
        industry_counts: dict[str, int] = {}
        for row in ranked.head(self.config.entry_rank).to_dict("records"):
            symbol = str(row["symbol"])
            industry = str(row.get("industry") or "")
            if industry and industry_counts.get(industry, 0) >= self.config.max_positions_per_industry:
                rejections[symbol] = "行业仓位约束未入选"
                continue
            correlated = False
            for held in targets:
                pair = pd.concat(
                    [return_history.get(symbol), return_history.get(held)], axis=1
                ).dropna()
                if len(pair) >= 40 and float(pair.corr().iloc[0, 1]) > self.config.max_pairwise_correlation:
                    correlated = True
                    break
            if correlated:
                rejections[symbol] = "与已选标的相关性过高"
                continue
            targets.append(symbol)
            if industry:
                industry_counts[industry] = industry_counts.get(industry, 0) + 1
            if len(targets) >= self.config.target_count:
                break
        if not targets:
            return [], {}, rejections
        volatility = ranked.set_index("symbol")["volatility"].astype(float)
        inverse = {symbol: 1.0 / max(float(volatility.loc[symbol]), 0.05) for symbol in targets}
        denominator = sum(inverse.values()) or 1.0
        weights = {
            symbol: min(
                self.config.max_single_position_pct,
                self.config.target_total_exposure_pct * inverse[symbol] / denominator,
            )
            for symbol in targets
        }
        return targets, weights, rejections
