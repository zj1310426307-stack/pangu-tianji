from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from .data_center import DATA_CENTER_SCHEMA_VERSION, DataCenter, DataCenterError
from .data_monitor import DataIntelligenceService, DataQualityGateError
from .data_monitor.contracts import DataIntelligenceError
from .execution_rules import assess_tradeability
from .investment_profile import InvestmentProfile
from .research_pipeline import (
    MAINBOARD_PREFIXES,
    STRATEGY_VERSION,
    ResearchPipeline,
    ResearchPipelineConfig,
    ResearchPipelineError,
)
from .services.portfolio_risk_engine import PortfolioRiskEngine
from .services.portfolio_risk_store import PortfolioRiskStore, PortfolioRiskStoreError
from .services.portfolio_service import PortfolioService, PortfolioServiceError


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _finite_float(value: Any, default: float = 0.0) -> float:
    """Return a JSON-safe finite float for published research evidence."""
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


class ThsResearchError(RuntimeError):
    """Represent a credential-free failure from a research capability."""


class ThsStockResearchClient:
    """Read the documented THS stock research APIs without persisting secrets."""

    def __init__(self, base_url: str, api_key_env: str, timeout: int = 20, client: Any | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.client = client or httpx.Client(follow_redirects=False, trust_env=False)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return one successful data object and retain only safe error context."""
        key = os.getenv(self.api_key_env, "").strip()
        if not key:
            raise ThsResearchError(f"未配置环境变量{self.api_key_env}")
        response = self.client.get(
            f"{self.base_url}{path}", headers={"X-api-key": key}, params=params or {}, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            code = payload.get("code", -1) if isinstance(payload, dict) else -1
            message = payload.get("message", "响应结构无效") if isinstance(payload, dict) else "响应结构无效"
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            suffix = f" request_id={request_id}" if request_id else ""
            raise ThsResearchError(f"同花顺接口失败 code={code} message={message}{suffix}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ThsResearchError("同花顺接口缺少data对象")
        return data

    def tickers(self) -> list[dict[str, Any]]:
        """Fetch the complete A-share ticker catalog using documented pagination."""
        items: list[dict[str, Any]] = []
        offset, limit = 0, 10000
        while True:
            page = self._get("/api/meta/tickers/list", {"asset_type": "a-share", "limit": limit, "offset": offset})
            batch = page.get("item")
            if not isinstance(batch, list):
                raise ThsResearchError("标的列表item无效")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < limit:
                break
            offset += limit
        return items

    def snapshot(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Fetch either a full-market page or an explicit batch of current quotes."""
        if symbols:
            return self._get("/api/a-share/prices/snapshot", {"thscodes": ",".join(symbols)})
        items: list[dict[str, Any]] = []
        offset, limit, latest_timestamp = 0, 10000, None
        while True:
            page = self._get("/api/a-share/prices/snapshot", {"limit": limit, "offset": offset})
            batch = page.get("item")
            if not isinstance(batch, list):
                raise ThsResearchError("全市场行情快照item无效")
            items.extend(item for item in batch if isinstance(item, dict))
            latest_timestamp = page.get("timestamp", latest_timestamp)
            total = page.get("total")
            if isinstance(total, int) and len(items) >= total:
                break
            if len(batch) < limit:
                break
            offset += limit
        if isinstance(total, int) and len(items) < total:
            raise ThsResearchError(f"全市场行情分页不完整：{len(items)}/{total}")
        return {"item": items, "total": len(items), "timestamp": latest_timestamp}

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Fetch forward-adjusted daily bars for one stock."""
        def ms(day: date) -> int:
            dt = datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI_TZ)
            return int(dt.timestamp() * 1000)

        data = self._get(
            "/api/a-share/prices/historical",
            {"thscode": symbol, "interval": "1d", "start": ms(start), "end": ms(end), "adjust": "forward"},
        )
        frame = pd.DataFrame(data.get("item") or [])
        required = {"date_ms", "close_price", "volume"}
        if not required.issubset(frame.columns):
            raise ThsResearchError(f"{symbol}历史行情字段不完整")
        frame = frame.sort_values("date_ms").reset_index(drop=True)
        frame["close_price"] = pd.to_numeric(frame["close_price"], errors="coerce")
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        frame["turnover"] = pd.to_numeric(
            frame.get("turnover", frame["close_price"] * frame["volume"]), errors="coerce"
        )
        if frame["close_price"].isna().any() or (frame["close_price"] <= 0).any():
            raise ThsResearchError(f"{symbol}历史收盘价无效")
        return frame

    def indicators(self, symbol: str, report: str) -> dict[str, float]:
        """Flatten documented financial indicator blocks into numeric values."""
        data = self._get("/api/a-share/financials/indicators", {"thscode": symbol, "report": report})
        values: dict[str, float] = {}
        for ability in data.get("abilities") or []:
            for item in ability.get("indicators") or []:
                try:
                    value = float(item.get("value"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    values[str(item.get("index_id"))] = value
        aliases = {
            "net_profit_yoy_growth_ratio": "calculate_parent_holder_net_profit_yoy_growth_ratio",
            "operating_income_yoy_growth_ratio": "calculate_operating_income_yoy_growth_ratio",
            "earnings_yield": "earnings_yield",
            "book_to_price": "book_to_price",
            "free_cash_flow_yield": "free_cash_flow_yield",
            "dividend_yield": "dividend_yield",
            "debt_ratio": "asset_liability_ratio",
            "accrual_ratio": "accrual_ratio",
        }
        for canonical, documented_id in aliases.items():
            if documented_id in values:
                values[canonical] = values[documented_id]
        return values

    def hot_symbols(self) -> tuple[set[str], list[str]]:
        """Fetch optional day hot lists; failures become explicit context warnings."""
        symbols: set[str] = set()
        warnings: list[str] = []
        for path in ("/api/a-share/special-data/hot-stock-list", "/api/a-share/special-data/skyrocket-list"):
            try:
                data = self._get(path, {"period": "day"})
                symbols.update(str(item.get("thscode")) for item in data.get("item") or [] if item.get("thscode"))
            except Exception as exc:
                warnings.append(str(exc)[:300])
        return symbols, warnings

    def trading_days(self) -> set[str]:
        """Return the official recent A-share trading-day set as ISO dates."""
        data = self._get("/api/a-share/calendar/trading-days")
        result = set()
        for item in data.get("item") or []:
            raw = str(item.get("date", ""))
            if len(raw) == 8 and raw.isdigit():
                result.add(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        return result



def report_period(as_of: date) -> str:
    """Choose the latest report period whose statutory window has fully elapsed."""
    if as_of >= date(as_of.year, 11, 1):
        return f"{as_of.year}-3"
    if as_of >= date(as_of.year, 9, 1):
        return f"{as_of.year}-2"
    if as_of >= date(as_of.year, 5, 1):
        return f"{as_of.year}-1"
    return f"{as_of.year - 1}-3"


class ThsResearchDataSource:
    """Adapt THS responses to the provider-neutral Research Pipeline contract."""

    def __init__(
        self,
        client: ThsStockResearchClient,
        config: dict[str, Any],
        *,
        as_of: date,
        preview: bool,
    ) -> None:
        self.client = client
        self.config = config
        self.as_of = pd.Timestamp(as_of).normalize()
        self.preview = preview
        self.snapshot_at: datetime | None = None
        self.quote_timestamp_ms: int | None = None
        self.catalog_count = 0
        self.report = report_period(as_of)
        self.warnings: list[str] = []
        self._quotes: dict[str, dict[str, Any]] = {}

    def security_universe(self, as_of: pd.Timestamp) -> pd.DataFrame:
        """Return the THS ticker catalog in the shared security-master schema."""
        if as_of != self.as_of:
            raise ResearchPipelineError("同花顺适配器研究日期不一致")
        tickers = self.client.tickers()
        self.catalog_count = len(tickers)
        frame = pd.DataFrame(tickers)
        if frame.empty or "thscode" not in frame:
            raise ResearchPipelineError("同花顺股票代码表为空或缺少thscode")
        frame = frame.rename(columns={"thscode": "symbol"})
        if "name" not in frame:
            frame["name"] = frame["symbol"]
        if "industry" not in frame:
            frame["industry"] = None
        return frame

    def data_snapshot(self, as_of: pd.Timestamp, universe: pd.DataFrame) -> pd.DataFrame:
        """Validate one market snapshot and expose normalized tradeability fields."""
        snapshot = self.client.snapshot()
        timestamp = snapshot.get("timestamp")
        if timestamp is None:
            raise ResearchPipelineError("全市场快照缺少时间戳，禁止生成计划")
        self.quote_timestamp_ms = int(timestamp)
        self.snapshot_at = datetime.fromtimestamp(
            self.quote_timestamp_ms / 1000, tz=timezone.utc
        ).astimezone(SHANGHAI_TZ)
        if self.snapshot_at.date() != as_of.date():
            raise ResearchPipelineError("全市场快照不属于当前研究日，禁止更新候选榜")
        close_time = datetime.strptime("15:00", "%H:%M").time()
        if not self.preview and self.snapshot_at.time() < close_time:
            raise ResearchPipelineError("全市场快照不是研究日完整收盘数据，禁止生成计划")

        catalog = universe.set_index("symbol", drop=False).to_dict("index")
        rows: list[dict[str, Any]] = []
        for quote in snapshot.get("item") or []:
            symbol = str(quote.get("thscode") or "")
            if not symbol:
                continue
            ticker = catalog.get(symbol, {})
            enriched = {
                **quote,
                "listing_days": ticker.get("listing_days", quote.get("listing_days")),
                "is_st": "ST" in str(ticker.get("name") or "").upper(),
                "board": "mainboard",
            }
            self._quotes[symbol] = quote
            rows.append({
                **quote,
                "symbol": symbol,
                "last_price": quote.get("last_price"),
                "turnover": quote.get("turnover"),
                "volume": quote.get("volume"),
                "tradeable": assess_tradeability(enriched, "BUY").tradable,
            })
        return pd.DataFrame(rows)

    def price_history(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Load history and normalize completed/formal and synthetic/preview bars."""
        start = as_of.date() - timedelta(days=int(self.config["history_days"]))
        rows: list[pd.DataFrame] = []
        for symbol in symbols:
            try:
                bars = self.client.history(symbol, start, as_of.date()).copy()
            except Exception as exc:
                self.warnings.append(f"{symbol}历史行情失败：{str(exc)[:180]}")
                continue
            dates = (
                pd.to_datetime(bars["date_ms"], unit="ms", utc=True)
                .dt.tz_convert(SHANGHAI_TZ)
                .dt.tz_localize(None)
                .dt.normalize()
            )
            normalized = pd.DataFrame({
                "date": dates,
                "symbol": symbol,
                "close": pd.to_numeric(bars["close_price"], errors="coerce"),
                "turnover": pd.to_numeric(bars["turnover"], errors="coerce"),
            })
            if self.preview:
                normalized = normalized[normalized["date"] < as_of]
                quote = self._quotes.get(symbol, {})
                normalized = pd.concat(
                    [
                        normalized,
                        pd.DataFrame([{
                            "date": as_of,
                            "symbol": symbol,
                            "close": quote.get("last_price"),
                            "turnover": quote.get("turnover"),
                        }]),
                    ],
                    ignore_index=True,
                )
            rows.append(normalized)
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
            columns=["date", "symbol", "close", "turnover"]
        )

    def fundamentals(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Map THS indicator names to the shared point-in-time financial schema."""
        required = (
            "net_profit_yoy_growth_ratio",
            "operating_income_yoy_growth_ratio",
            "index_weighted_avg_roe",
            "net_profit_cash_content",
        )
        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                values = self.client.indicators(symbol, self.report)
            except Exception as exc:
                self.warnings.append(f"{symbol}财务指标失败：{str(exc)[:180]}")
                continue
            present = {key: values[key] for key in required if key in values}
            if len(present) != len(required):
                continue
            rows.append({
                "symbol": symbol,
                "available_at": as_of,
                "profit_growth": present[required[0]],
                "revenue_growth": present[required[1]],
                "roe": present[required[2]],
                "cash_quality": present[required[3]],
                **{key: values.get(key) for key in (
                    "earnings_yield",
                    "book_to_price",
                    "free_cash_flow_yield",
                    "dividend_yield",
                    "debt_ratio",
                    "accrual_ratio",
                    "roe_stability",
                    "gross_margin_stability",
                    "profit_cagr_3y",
                    "revenue_cagr_3y",
                    "market_cap",
                )},
            })
        return pd.DataFrame(rows)


class DailyStockResearch:
    """Publish live and preview plans through the same Research Pipeline as backtests."""

    MAINBOARD_PREFIXES = MAINBOARD_PREFIXES

    def __init__(
        self,
        client: ThsStockResearchClient,
        config: dict[str, Any],
        output_dir: Path,
        investment_profile: dict[str, Any] | None = None,
        default_capital: float = 100_000.0,
        portfolio_store: PortfolioRiskStore | None = None,
        data_intelligence: DataIntelligenceService | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.output_dir = output_dir
        self.pipeline = ResearchPipeline(ResearchPipelineConfig.from_mapping(config))
        self.data_center = DataCenter(self.output_dir / "data_center")
        project_root = (
            self.output_dir.parent if self.output_dir.name.lower() == "output"
            else self.output_dir
        )
        self.data_intelligence = data_intelligence or DataIntelligenceService(
            project_root,
            data_center_root=self.data_center.root,
        )
        self.investment_profile = InvestmentProfile.from_mapping(
            investment_profile,
            default_capital=default_capital,
        )
        self.portfolio_service = PortfolioService()
        self.portfolio_risk_engine = PortfolioRiskEngine()
        self.portfolio_store = portfolio_store or PortfolioRiskStore(
            self.output_dir / "portfolio_risk_center.db"
        )

    def run(
        self,
        as_of: date | None = None,
        force: bool = False,
        preview: bool = False,
    ) -> dict[str, Any]:
        """Run the unified strategy and publish a formal plan or read-only preview."""
        as_of = as_of or datetime.now(SHANGHAI_TZ).date()
        existing = self._existing_formal_plan(as_of, force=force, preview=preview)
        if existing is not None:
            return existing
        if as_of.isoformat() not in self.client.trading_days():
            raise ThsResearchError(f"{as_of.isoformat()}不是官方交易日，跳过每日研究")
        source = ThsResearchDataSource(
            self.client, self.config, as_of=as_of, preview=preview
        )
        try:
            pipeline_run = self.data_center.execute_pipeline(
                self.pipeline,
                pd.Timestamp(as_of),
                source,
                run_kind="intraday_preview" if preview else "formal_close_plan",
                metadata=lambda: {
                    "observed_at": (
                        source.snapshot_at.isoformat() if source.snapshot_at else None
                    ),
                    "quote_timestamp_ms": source.quote_timestamp_ms,
                    "report_period": source.report,
                    "provider": "ths_finance",
                    "preview": preview,
                },
            )
            result = pipeline_run.result
            data_run = pipeline_run.record
            data_health = self.data_intelligence.evaluate_run(
                data_run.run_id,
                preview=preview,
                enforce=not preview,
            )
            security_risks = [
                self.portfolio_risk_engine.security_risk(row)
                for row in result.ranked.to_dict("records")
            ]
            target_portfolio = self.portfolio_service.build_target_portfolio(
                result,
                self.data_center.manifest(data_run.run_id),
                self.investment_profile,
                security_risks,
            )
            portfolio_risk = self.portfolio_risk_engine.assess_target_portfolio(
                result.ranked,
                target_portfolio,
                self.investment_profile,
            )
        except (
            ResearchPipelineError,
            DataCenterError,
            DataIntelligenceError,
            DataQualityGateError,
            PortfolioServiceError,
            PortfolioRiskStoreError,
        ) as exc:
            raise ThsResearchError(str(exc)) from exc

        hot, context_warnings = self.client.hot_symbols()
        context_warnings.extend(source.warnings)
        risk_by_symbol = {
            str(item["symbol"]): item
            for item in security_risks
        }
        portfolio_weight_by_symbol = {
            str(item["symbol"]): float(item["target_weight"])
            for item in target_portfolio["positions"]
        }
        candidates: list[dict[str, Any]] = []
        for row in result.ranked.to_dict("records"):
            symbol = str(row["symbol"])
            risk_flags = (
                (["热榜标的，仅作风险提示"] if symbol in hot else [])
                + ([] if bool(row["valuation_complete"]) else ["估值字段缺失，价值分采用中性值"])
                + ([] if row.get("industry") else ["数据源未提供个股行业归属，行业中性化未生效"])
            )
            rejection = result.portfolio_rejections.get(symbol)
            if rejection:
                risk_flags.append(rejection)
            candidates.append({
                "rank": int(row["rank"]),
                "symbol": symbol,
                "name": str(row.get("name", symbol)),
                "score": round(float(row["score"]), 4),
                "price": float(row["last_price"]),
                "turnover": float(row["turnover"]),
                "components": {
                    "value": round(float(row["points_value"]), 4),
                    "quality": round(float(row["points_quality"]), 4),
                    "growth": round(float(row["points_growth"]), 4),
                    "momentum": round(float(row["points_momentum"]), 4),
                    "trend": round(float(row["points_trend"]), 4),
                    "low_risk": round(float(row["points_low_risk"]), 4),
                    "liquidity": round(float(row["points_liquidity"]), 4),
                    "technical_total": round(float(
                        row["points_momentum"]
                        + row["points_trend"]
                        + row["points_low_risk"]
                        + row["points_liquidity"]
                    ), 4),
                    "financial_total": round(float(
                        row["points_value"] + row["points_quality"] + row["points_growth"]
                    ), 4),
                },
                "risk_flags": risk_flags,
                "reason": (
                    f"七维综合得分{float(row['score']):.2f}；"
                    f"中期动量{float(row['momentum']):.2%}；"
                    f"120日回撤{float(row['max_drawdown']):.2%}"
                ),
                "invalidation": (
                    "报价过期、停牌、接近涨跌停、财务数据缺失、"
                    f"跌破8%止损线或组合回撤达到{self.investment_profile.max_drawdown_tolerance:.0%}"
                ),
                "industry": row.get("industry"),
                "market_cap": _finite_float(row.get("market_cap")),
                "volatility": float(row["volatility"]),
                "valuation_complete": bool(row["valuation_complete"]),
                "target_weight": float(portfolio_weight_by_symbol.get(symbol, 0.0)),
                "security_risk": risk_by_symbol.get(symbol),
                "signals": {
                    "momentum": _finite_float(row.get("momentum")),
                    "trend_strength": _finite_float(row.get("trend_strength")),
                    "ma20": _finite_float(row.get("ma20")),
                    "ma60": _finite_float(row.get("ma60")),
                    "profit_growth": _finite_float(row.get("profit_growth")),
                    "revenue_growth": _finite_float(row.get("revenue_growth")),
                    "roe": _finite_float(row.get("roe")),
                    "cash_quality": _finite_float(row.get("cash_quality")),
                    "net_inflow_20d_ratio": _finite_float(
                        row.get("net_inflow_20d_ratio")
                    ),
                },
            })

        if not result.data_quality["industry_neutralization"]:
            context_warnings.append(
                "同花顺当前股票代码表未提供可验证的个股行业归属，本次未做行业中性化"
            )
        formal_targets = [
            str(item["symbol"])
            for item in target_portfolio["positions"]
        ]
        generated_at = datetime.now(timezone.utc).isoformat()
        snapshot_at = source.snapshot_at
        if snapshot_at is None:
            raise ThsResearchError("统一Research Pipeline未返回行情时间")
        plan = {
            "version": "2.0.0",
            "strategy_version": STRATEGY_VERSION,
            "run_id": data_run.run_id,
            "data_version": data_run.data_version,
            "data_health": data_health,
            "data_center_schema_version": DATA_CENTER_SCHEMA_VERSION,
            "data_center_manifest": data_run.manifest_path.relative_to(
                self.output_dir
            ).as_posix(),
            "factor_model_version": result.factor_model_version,
            "factor_contract_hash": result.factor_contract_hash,
            "investment_profile": self.investment_profile.to_dict(),
            "target_portfolio": target_portfolio,
            "portfolio_risk": portfolio_risk,
            "research_pipeline": [
                "Security Universe",
                "Data Snapshot",
                "Feature Calculation",
                "Factor Score",
                "Ranking",
                "Portfolio Construction",
            ],
            "stage_counts": result.stage_counts,
            "mode": "intraday_preview" if preview else "formal_close_plan",
            "used_for_execution": not preview,
            "research_date": as_of.isoformat(),
            "generated_at": generated_at,
            "observed_at": snapshot_at.isoformat(),
            "quote_timestamp_ms": source.quote_timestamp_ms,
            "universe_count": source.catalog_count,
            "eligible_count": result.stage_counts["data_snapshot"],
            "report_period": source.report,
            "context_warnings": context_warnings,
            "candidates": candidates,
            "targets": [] if preview else formal_targets,
            "preview_leaders": formal_targets if preview else [],
            "execution_ready": False if preview else len(formal_targets) >= min(
                3, self.pipeline.config.target_count
            ),
            "data_quality": {
                **result.data_quality,
                "snapshot_trade_date": snapshot_at.date().isoformat(),
                "snapshot_after_close": snapshot_at.time()
                >= datetime.strptime("15:00", "%H:%M").time(),
            },
            "provenance": (
                "同花顺盘中全市场快照、截至上一交易日的前复权日线及最近已披露财务指标；"
                "当前快照仅作临时观测，不覆盖正式计划、不用于执行；"
                "实时与回测共用Research Pipeline"
                if preview
                else
                "同花顺全市场收盘快照、点时前复权日线、最近已披露财务指标；"
                "实时与回测共用Research Pipeline及保留的七维因子合同"
            ),
        }
        try:
            plan["portfolio_evidence"] = self.portfolio_store.save_research_snapshot(
                target_portfolio,
                portfolio_risk,
                name=("盘古天机盘中预览组合" if preview else "盘古天机正式目标组合"),
            )
        except PortfolioRiskStoreError as exc:
            raise ThsResearchError(str(exc)) from exc
        return self._publish(plan, as_of, force=force, preview=preview)

    def _existing_formal_plan(
        self,
        as_of: date,
        *,
        force: bool,
        preview: bool,
    ) -> dict[str, Any] | None:
        """Reuse a complete immutable close plan without creating a duplicate research run."""
        if force or preview:
            return None
        target = self.output_dir / "research" / "history" / f"{as_of.isoformat()}.json"
        if not target.exists():
            return None
        existing = json.loads(target.read_text(encoding="utf-8"))
        if (
            existing.get("research_date") == as_of.isoformat()
            and existing.get("strategy_version") == STRATEGY_VERSION
            and existing.get("run_id")
            and existing.get("data_version")
        ):
            return existing
        return None

    def _publish(
        self,
        plan: dict[str, Any],
        as_of: date,
        *,
        force: bool,
        preview: bool,
    ) -> dict[str, Any]:
        """Atomically publish previews separately from executable close plans."""
        if preview:
            preview_path = self.output_dir / "research" / "preview.json"
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = preview_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(preview_path)
            return plan

        history_dir = self.output_dir / "research" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        target = history_dir / f"{as_of.isoformat()}.json"
        if target.exists() and not force:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("research_date") == plan["research_date"]:
                return existing
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        latest = self.output_dir / "research" / "latest.json"
        latest_tmp = latest.with_suffix(".json.tmp")
        latest_tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_tmp.replace(latest)
        return plan
