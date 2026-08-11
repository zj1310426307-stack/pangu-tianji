from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml

from ..paper_portfolio import PaperPortfolio
from ..execution_rules import assess_tradeability
from ..cross_sectional_backtest import point_in_time_dataset_status
from ..investment_profile import InvestmentProfile
from ..stock_research import DailyStockResearch, ThsStockResearchClient
from .exit_engine import ExitEngine
from .portfolio_risk_engine import PortfolioRiskEngine
from .portfolio_risk_store import PortfolioRiskStore
from .portfolio_service import PortfolioService
from .valuation_service import ValuationService


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class DailyResearchService:
    """Own daily research, paper execution and monitoring as separate workflows."""

    def __init__(
        self,
        project_root: Path,
        client: ThsStockResearchClient | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.raw = yaml.safe_load((self.root / "config" / "settings.yaml").read_text(encoding="utf-8"))
        data = self.raw["data"]
        self.client = client or ThsStockResearchClient(
            data["base_url"], data["api_key_env"], int(data["request_timeout_seconds"])
        )
        self.investment_profile = InvestmentProfile.from_mapping(
            self.raw.get("investment_profile"),
            default_capital=float(self.raw["account"]["initial_cash"]),
        )
        self.portfolio_service = PortfolioService()
        self.portfolio_risk_engine = PortfolioRiskEngine()
        self.exit_engine = ExitEngine()
        self.portfolio_store = PortfolioRiskStore(
            self.root / "output" / "portfolio_risk_center.db"
        )
        self.research = DailyStockResearch(
            self.client,
            self.raw["research"],
            self.root / "output",
            self.raw.get("investment_profile"),
            float(self.raw["account"]["initial_cash"]),
            self.portfolio_store,
        )
        self._now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self.valuation_service = ValuationService(
            float(self.raw["paper_account"]["initial_cash"]),
            self.review_valuation,
        )
        self.effective_paper_config = dict(self.raw["paper_account"])
        self.effective_paper_config["target_position_pct"] = min(
            float(self.effective_paper_config["target_position_pct"]),
            self.investment_profile.maximum_single_weight,
        )
        self.effective_paper_config["max_total_exposure_pct"] = min(
            float(self.effective_paper_config["max_total_exposure_pct"]),
            self.investment_profile.maximum_exposure,
        )
        self.effective_paper_config["max_drawdown_pct"] = min(
            float(self.effective_paper_config["max_drawdown_pct"]),
            self.investment_profile.max_drawdown_tolerance,
        )
        self.paper = PaperPortfolio(
            self.root / "output" / "paper_account.db",
            self.effective_paper_config,
            self.raw["costs"],
            self.valuation_service,
        )
        self._lock = Lock()
        self._last_error: str | None = None
        self._last_action: str | None = None
        self._ticker_by_symbol: dict[str, dict[str, Any]] | None = None
        self._trading_day_cache: tuple[str, set[str]] | None = None

    def _now(self) -> datetime:
        value = self._now_provider()
        return value.astimezone(SHANGHAI_TZ) if value.tzinfo else value.replace(tzinfo=SHANGHAI_TZ)

    @property
    def paper_lock(self) -> Lock:
        """Share the account workflow lock with read-only review composition."""
        return self._lock

    def _status_path(self) -> Path:
        return self.root / "output" / "research" / "status.json"

    def _write_collection_status(self, state: str, message: str | None = None) -> None:
        path = self._status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "research_date": self._now().date().isoformat(), "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(), "message": message,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _latest_plan(self) -> dict[str, Any]:
        """Load the formal close plan that alone may feed paper automation."""
        path = self.root / "output" / "research" / "latest.json"
        if not path.exists():
            raise RuntimeError("尚无每日研究计划")
        return json.loads(path.read_text(encoding="utf-8"))

    def _latest_preview(self) -> dict[str, Any] | None:
        """Load the separately persisted intraday ranking preview when available."""
        path = self.root / "output" / "research" / "preview.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def collect(self, force: bool = False) -> dict[str, Any]:
        """Run one serialized end-of-day collection without changing the account."""
        with self._lock:
            try:
                if self._now().time() < time(15, 10):
                    raise RuntimeError("每日研究只能在交易日15:10后生成，盘中验收数据不可进入次日计划")
                result = self.research.run(self._now().date(), force=force)
                self._last_action, self._last_error = "collect", None
                self._write_collection_status("succeeded")
                return result
            except Exception as exc:
                self._last_error = str(exc)[:1000]
                self._write_collection_status("failed", self._last_error)
                raise

    def refresh_preview(self) -> dict[str, Any]:
        """Refresh a current-day candidate preview without mutating the formal plan or account."""
        with self._lock:
            try:
                now = self._now()
                if now.date().isoformat() not in self._trading_days():
                    raise RuntimeError("今天不是官方交易日，无法刷新盘中候选榜")
                result = self.research.run(now.date(), force=True, preview=True)
                self._last_action, self._last_error = "refresh_intraday_candidates", None
                return result
            except Exception as exc:
                self._last_error = str(exc)[:1000]
                raise

    def _fresh_quotes(self, symbols: list[str]) -> tuple[dict[str, dict[str, Any]], int]:
        data = self.client.snapshot(symbols)
        timestamp = data.get("timestamp")
        if timestamp is None:
            raise RuntimeError("行情快照没有有效时间")
        observed = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
        age = (self._now().astimezone(timezone.utc) - observed).total_seconds()
        if age < -30 or age > int(self.raw["research"]["quote_max_age_seconds"]):
            raise RuntimeError(f"行情快照时效异常（相差{age:.0f}秒），禁止模拟成交")
        items = data.get("item") or []
        quotes = {str(item.get("thscode")): item for item in items if item.get("thscode")}
        missing = sorted(set(symbols) - set(quotes))
        if missing:
            raise RuntimeError(f"行情快照缺少{len(missing)}个标的，禁止模拟成交")
        return quotes, int(timestamp)

    def _ticker_details(self, symbol: str) -> dict[str, Any]:
        """Resolve and validate one Shanghai/Shenzhen main-board stock."""
        normalized = symbol.strip().upper()
        code = normalized.split(".", 1)[0]
        if (
            len(normalized) != 9
            or not normalized.endswith((".SH", ".SZ"))
            or not code.isdigit()
            or not code.startswith(DailyStockResearch.MAINBOARD_PREFIXES)
        ):
            raise RuntimeError("仅允许沪深主板股票代码，例如600519.SH或000001.SZ")
        if self._ticker_by_symbol is None:
            self._ticker_by_symbol = {
                str(item.get("thscode", "")).upper(): item
                for item in self.client.tickers()
                if item.get("thscode")
            }
        ticker = self._ticker_by_symbol.get(normalized)
        if not ticker:
            raise RuntimeError("同花顺A股代码表中不存在该股票")
        name = str(ticker.get("name") or normalized)
        if "ST" in name.upper() or "退" in name:
            raise RuntimeError("ST或退市整理股票禁止模拟买入")
        return {**ticker, "thscode": normalized, "name": name}

    def _trading_days(self) -> set[str]:
        """Cache the official calendar for one local date to limit API traffic."""
        today = self._now().date().isoformat()
        if self._trading_day_cache is None or self._trading_day_cache[0] != today:
            self._trading_day_cache = (today, set(self.client.trading_days()))
        return self._trading_day_cache[1]

    def _market_is_open(self, now: datetime) -> bool:
        """Return whether the official A-share continuous trading session is open."""
        current = now.time().replace(tzinfo=None)
        in_session = time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
        return now.date().isoformat() in self._trading_days() and in_session

    def _quote_view(
        self,
        *,
        ticker: dict[str, Any],
        quote: dict[str, Any],
        timestamp: int,
        now: datetime,
    ) -> dict[str, Any]:
        """Shape one documented THS snapshot and derive paper-order readiness."""
        numeric_keys = (
            "last_price", "price_change", "price_change_ratio_pct", "open_price",
            "high_price", "low_price", "prev_price", "volume", "turnover",
        )
        values: dict[str, float] = {}
        for key in numeric_keys:
            try:
                value = float(quote.get(key))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"同花顺行情字段{key}无效") from exc
            if not math.isfinite(value):
                raise RuntimeError(f"同花顺行情字段{key}不是有限数值")
            values[key] = value
        observed = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        age = (now.astimezone(timezone.utc) - observed).total_seconds()
        stale = age < -30 or age > int(self.raw["research"]["quote_max_age_seconds"])
        market_open = self._market_is_open(now)
        blocked_reason: str | None = None
        sell_blocked_reason: str | None = None
        account_view = self.paper.snapshot()
        position = next(
            (item for item in account_view["positions"] if item["symbol"] == ticker["thscode"]),
            None,
        )
        available_quantity = int(position["sellable_quantity"]) if position else 0
        max_buy_quantity = self.valuation_service.round_lot_buying_power(
            float(account_view["available_cash"]), values["last_price"]
        )
        if account_view["kill_switch"]:
            blocked_reason = "Kill Switch已开启"
        if not market_open:
            blocked_reason = "当前不在A股交易日盘中时段"
            sell_blocked_reason = blocked_reason
        elif stale:
            blocked_reason = f"行情快照已过期（相差{age:.0f}秒）"
            sell_blocked_reason = blocked_reason
        else:
            buy_tradeability = assess_tradeability(
                {**quote, "board": "mainboard", "is_st": "ST" in str(ticker.get("name", "")).upper()},
                "BUY",
            )
            sell_tradeability = assess_tradeability(
                {**quote, "board": "mainboard", "is_st": "ST" in str(ticker.get("name", "")).upper()},
                "SELL",
            )
            if not buy_tradeability.tradable:
                blocked_reason = buy_tradeability.reason
            if available_quantity <= 0:
                sell_blocked_reason = "无可卖持仓，或当日买入受T+1限制"
            elif not sell_tradeability.tradable:
                sell_blocked_reason = sell_tradeability.reason
        return {
            "symbol": ticker["thscode"], "name": ticker["name"],
            "source": "ths_finance_snapshot", "feed_type": "polling_snapshot",
            "timestamp_ms": timestamp, "observed_at": observed.astimezone(SHANGHAI_TZ).isoformat(),
            "age_seconds": round(age, 3), "stale": stale, "market_open": market_open,
            **values,
            "can_submit_paper_order": blocked_reason is None or sell_blocked_reason is None,
            "can_submit_paper_buy": blocked_reason is None,
            "can_submit_paper_sell": sell_blocked_reason is None,
            "available_quantity": available_quantity,
            "max_buy_quantity": max_buy_quantity,
            "blocked_reason": blocked_reason,
            "sell_blocked_reason": sell_blocked_reason,
            "live_trading_enabled": False, "can_submit_orders": False,
        }

    def quote(self, symbol: str) -> dict[str, Any]:
        """Fetch one current THS polling snapshot without changing account state."""
        with self._lock:
            ticker = self._ticker_details(symbol)
            data = self.client.snapshot([ticker["thscode"]])
            timestamp = data.get("timestamp")
            items = data.get("item") or []
            quote = next(
                (item for item in items if str(item.get("thscode", "")).upper() == ticker["thscode"]),
                None,
            )
            if timestamp is None or not isinstance(quote, dict):
                raise RuntimeError("同花顺行情快照缺少所选股票")
            return self._quote_view(
                ticker=ticker, quote=quote, timestamp=int(timestamp), now=self._now()
            )

    def review_valuation(self, symbols: list[str]) -> dict[str, Any]:
        """Return read-only prices for actual holdings, with explicit freshness.

        Unlike an executable quote, this method does not require the market to
        be open and does not reject an older close. The workbench labels stale
        data and falls back to persisted NAV when the supplier is unavailable.
        It intentionally performs no account or order mutation.
        """
        normalized = sorted({str(symbol).strip().upper() for symbol in symbols if symbol})
        if not normalized:
            return {
                "prices": {},
                "source": "ths_finance_snapshot",
                "observed_at": None,
                "age_seconds": None,
                "stale": True,
                "message": "模拟账户当前空仓，无需读取持仓行情",
            }
        data = self.client.snapshot(normalized)
        timestamp = data.get("timestamp")
        if timestamp is None:
            raise RuntimeError("持仓行情没有有效时间")
        quotes = {
            str(item.get("thscode", "")).upper(): item
            for item in (data.get("item") or [])
            if item.get("thscode")
        }
        missing = sorted(set(normalized) - set(quotes))
        if missing:
            raise RuntimeError(f"持仓行情缺少{len(missing)}只股票")
        prices: dict[str, float] = {}
        for symbol in normalized:
            try:
                price = float(quotes[symbol].get("last_price"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"{symbol}持仓估值价格无效") from exc
            if not math.isfinite(price) or price <= 0:
                raise RuntimeError(f"{symbol}持仓估值价格无效")
            prices[symbol] = price
        observed = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
        age = (self._now().astimezone(timezone.utc) - observed).total_seconds()
        stale = age < -30 or age > int(self.raw["research"]["quote_max_age_seconds"])
        return {
            "prices": prices,
            "source": "ths_finance_snapshot",
            "observed_at": observed.astimezone(SHANGHAI_TZ).isoformat(),
            "age_seconds": round(age, 3),
            "stale": stale,
            "message": (
                f"同花顺持仓轮询快照，距当前{age:.0f}秒"
                + ("；非交易时段或数据已过期" if stale else "")
            ),
        }

    def preview_manual_order(self, side: str, symbol: str, quantity: int) -> dict[str, Any]:
        """Requote and return a paper-order estimate without inserting an order."""
        with self._lock:
            now = self._now()
            if not bool(self.raw["paper_account"].get("enabled")):
                raise RuntimeError("本地模拟账户未启用")
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股交易日09:30–11:30或13:00–15:00")
            ticker = self._ticker_details(symbol)
            symbols = sorted(
                {ticker["thscode"]}
                | {str(item["symbol"]) for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            result = self.paper.assess_manual_order(
                side=side,
                symbol=ticker["thscode"],
                name=ticker["name"],
                quantity=quantity,
                quotes=quotes,
                trade_date=now.date(),
            )
            result["quote_timestamp_ms"] = timestamp
            return result

    def preview_broker_order(
        self,
        side: str,
        symbol: str,
        quantity: int,
        order_type: str,
        limit_price: float | None,
    ) -> dict[str, Any]:
        """Preview one snapshot-backed broker-style paper order."""
        with self._lock:
            now = self._now()
            if not bool(self.raw["paper_account"].get("enabled")):
                raise RuntimeError("本地模拟账户未启用")
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股交易日09:30–11:30或13:00–15:00")
            ticker = self._ticker_details(symbol)
            symbols = sorted(
                {ticker["thscode"]}
                | {str(item["symbol"]) for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            result = self.paper.assess_manual_order(
                side=side,
                symbol=ticker["thscode"],
                name=ticker["name"],
                quantity=quantity,
                quotes=quotes,
                trade_date=now.date(),
                order_type=order_type,
                limit_price=limit_price,
            )
            result["quote_timestamp_ms"] = timestamp
            return result

    def submit_broker_order(
        self,
        side: str,
        symbol: str,
        quantity: int,
        order_type: str,
        limit_price: float | None,
        request_key: str,
    ) -> dict[str, Any]:
        """Persist a local paper order and match it only against a fresh snapshot."""
        with self._lock:
            now = self._now()
            if not bool(self.raw["paper_account"].get("enabled")):
                raise RuntimeError("本地模拟账户未启用")
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股交易日09:30–11:30或13:00–15:00")
            ticker = self._ticker_details(symbol)
            symbols = sorted(
                {ticker["thscode"]}
                | {str(item["symbol"]) for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            observed = datetime.fromtimestamp(
                timestamp / 1000, tz=timezone.utc
            ).astimezone(SHANGHAI_TZ)
            result = self.paper.place_broker_order(
                side=side,
                symbol=ticker["thscode"],
                name=ticker["name"],
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                quotes=quotes,
                trade_date=now.date(),
                request_key=request_key,
                observed_at=observed,
            )
            result.update(
                {
                    "quote_timestamp_ms": timestamp,
                    "live_trading_enabled": False,
                    "can_submit_orders": False,
                }
            )
            self._last_action, self._last_error = "broker_paper_order", None
            return result

    def cancel_broker_order(self, client_order_id: str) -> dict[str, Any]:
        """Cancel one active paper order; no external order is ever addressed."""
        with self._lock:
            try:
                order = self.paper.cancel_broker_order(client_order_id, self._now())
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            self._last_action, self._last_error = "cancel_broker_paper_order", None
            return {
                "order": order,
                "account": self.paper.snapshot(),
                "live_trading_enabled": False,
                "can_submit_orders": False,
            }

    def match_broker_orders(self) -> dict[str, Any]:
        """Match active paper DAY orders against one fresh THS snapshot batch."""
        with self._lock:
            now = self._now()
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股连续交易时段，模拟撮合暂停")
            active = [
                item
                for item in self.paper.activity(500)["orders"]
                if item.get("status") in {"PENDING", "SUBMITTED"}
            ]
            symbols = sorted({str(item["symbol"]) for item in active})
            if not symbols:
                return {
                    "matched": [],
                    "waiting": [],
                    "expired_count": self.paper.expire_day_orders(now.date(), now),
                    "account": self.paper.snapshot(),
                    "matching_model": "ths_polling_snapshot_all_or_none",
                    "live_trading_enabled": False,
                    "can_submit_orders": False,
                }
            quotes, timestamp = self._fresh_quotes(symbols)
            observed = datetime.fromtimestamp(
                timestamp / 1000, tz=timezone.utc
            ).astimezone(SHANGHAI_TZ)
            result = self.paper.match_broker_orders(quotes, observed)
            result["quote_timestamp_ms"] = timestamp
            self._last_action, self._last_error = "match_broker_paper_orders", None
            return result

    def manual_buy(self, symbol: str, quantity: int, request_key: str) -> dict[str, Any]:
        """Requote server-side, enforce paper controls, and persist one local BUY."""
        with self._lock:
            now = self._now()
            if not bool(self.raw["paper_account"].get("enabled")):
                raise RuntimeError("本地模拟账户未启用")
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股交易日09:30–11:30或13:00–15:00")
            ticker = self._ticker_details(symbol)
            symbols = sorted(
                {ticker["thscode"]}
                | {str(item["symbol"]) for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            result = self.paper.manual_buy(
                symbol=ticker["thscode"],
                name=ticker["name"],
                quantity=quantity,
                quotes=quotes,
                trade_date=now.date(),
                request_key=request_key,
            )
            result.update({
                "quote_timestamp_ms": timestamp,
                "live_trading_enabled": False,
                "can_submit_orders": False,
            })
            result["portfolio_risk_evidence"] = self._record_portfolio_risk(
                "manual_buy"
            )
            self._last_action, self._last_error = "manual_paper_buy", None
            return result

    def manual_sell(self, symbol: str, quantity: int, request_key: str) -> dict[str, Any]:
        """Requote server-side and persist one reduce-only local paper SELL."""
        with self._lock:
            now = self._now()
            if not bool(self.raw["paper_account"].get("enabled")):
                raise RuntimeError("本地模拟账户未启用")
            if not self._market_is_open(now):
                raise RuntimeError("当前不在A股交易日09:30–11:30或13:00–15:00")
            ticker = self._ticker_details(symbol)
            symbols = sorted(
                {ticker["thscode"]}
                | {str(item["symbol"]) for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            result = self.paper.manual_sell(
                symbol=ticker["thscode"],
                name=ticker["name"],
                quantity=quantity,
                quotes=quotes,
                trade_date=now.date(),
                request_key=request_key,
            )
            result.update({
                "quote_timestamp_ms": timestamp,
                "live_trading_enabled": False,
                "can_submit_orders": False,
            })
            result["portfolio_risk_evidence"] = self._record_portfolio_risk(
                "manual_sell"
            )
            self._last_action, self._last_error = "manual_paper_sell", None
            return result

    def _validate_execution_date(self, plan: dict[str, Any], now: datetime) -> None:
        trading_days = sorted(day for day in self._trading_days() if day <= now.date().isoformat())
        if now.date().isoformat() not in trading_days:
            raise RuntimeError("今天不是官方交易日，禁止模拟执行")
        prior = [day for day in trading_days if day < now.date().isoformat()]
        if not prior or plan.get("research_date") != prior[-1]:
            raise RuntimeError("推荐只在下一个交易日有效；旧计划已作废，禁止补单")
        generated = datetime.fromisoformat(str(plan.get("generated_at")))
        generated = generated.astimezone(SHANGHAI_TZ) if generated.tzinfo else generated.replace(tzinfo=SHANGHAI_TZ)
        if generated.date().isoformat() != plan.get("research_date") or generated.time() < time(15, 10):
            raise RuntimeError("计划不是在收盘后生成，禁止模拟执行")
        configured = time.fromisoformat(str(self.raw["paper_account"]["execute_time"]))
        start = datetime.combine(now.date(), configured, tzinfo=SHANGHAI_TZ)
        window = int(self.raw["paper_account"].get("execution_window_minutes", 10)) * 60
        if not 0 <= (now - start).total_seconds() <= window:
            raise RuntimeError("已错过09:35模拟执行窗口，旧计划禁止补发")
        status_path = self._status_path()
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("research_date") == plan.get("research_date") and status.get("state") != "succeeded":
                raise RuntimeError("最近一次核心数据采集失败，自动模拟执行已关闭")

    def execute(self) -> dict[str, Any]:
        """Execute the previous trading day's valid plan in the 09:35 window."""
        with self._lock:
            plan, now = self._latest_plan(), self._now()
            self._validate_execution_date(plan, now)
            if self.paper.snapshot()["kill_switch"]:
                raise RuntimeError("Kill Switch已开启，禁止模拟执行")
            symbols = sorted(
                {item["symbol"] for item in plan["candidates"]}
                | {item["symbol"] for item in self.paper.positions()}
            )
            quotes, timestamp = self._fresh_quotes(symbols)
            result = self.paper.execute_plan(plan, quotes, now.date())
            result["quote_timestamp_ms"] = timestamp
            result["portfolio_risk_evidence"] = self._record_portfolio_risk(
                "automatic_rebalance",
                plan,
            )
            self._last_action, self._last_error = "execute", None
            return result

    def monitor(self) -> dict[str, Any]:
        """Match active orders, refresh holdings and enforce paper stop rules."""
        with self._lock:
            now = self._now()
            if (
                now.date().isoformat() not in self._trading_days()
                or not time(9, 35) <= now.time() <= time(14, 55)
            ):
                raise RuntimeError("当前不在交易日09:35–14:55监控窗口")
            try:
                plan = self._latest_plan()
            except RuntimeError:
                plan = {"candidates": []}
            active_orders = [
                item
                for item in self.paper.activity(500)["orders"]
                if item.get("status") in {"PENDING", "SUBMITTED"}
            ]
            symbols = sorted(
                {item["symbol"] for item in plan.get("candidates", [])}
                | {item["symbol"] for item in self.paper.positions()}
                | {str(item["symbol"]) for item in active_orders}
            )
            if not symbols:
                return {"observed_at": now.isoformat(), "events": []}
            quotes, timestamp = self._fresh_quotes(symbols)
            observed = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).astimezone(SHANGHAI_TZ)
            matching = self.paper.match_broker_orders(quotes, observed)
            events = self.paper.monitor(quotes, observed)
            self._last_action, self._last_error = "monitor", None
            return {
                "observed_at": observed.isoformat(),
                "quote_timestamp_ms": timestamp,
                "matching": matching,
                "events": events,
                "portfolio_risk_evidence": self._record_portfolio_risk(
                    "monitor",
                    plan,
                ),
            }

    def dashboard(self) -> dict[str, Any]:
        """Serialize account reads with quote, execution and review workflows."""
        with self._lock:
            return self._dashboard_unlocked()

    def _compose_portfolio_center(
        self,
        plan: dict[str, Any] | None,
        account: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Compose one account-aware risk view from service-owned contracts."""
        if not plan or not plan.get("target_portfolio") or not plan.get("portfolio_risk"):
            return None
        portfolio_risk = self.portfolio_risk_engine.with_valuation(
            plan["portfolio_risk"],
            account["asset_valuation"],
            self.investment_profile,
        )
        adjustments = self.portfolio_service.rebalance_actions(
            plan["target_portfolio"],
            account["positions"],
            self.investment_profile,
        )
        exit_plan = self.exit_engine.evaluate(
            account["positions"],
            plan.get("candidates", []),
            portfolio_risk.get("security_risks", []),
            self.investment_profile,
            portfolio_risk,
            hold_rank=int(self.raw["research"]["hold_rank"]),
        )
        return {
            "investment_profile": self.investment_profile.to_dict(),
            "target_portfolio": plan["target_portfolio"],
            "risk_assessment": portfolio_risk,
            "rebalance_actions": adjustments,
            "exit_plan": exit_plan,
            "creates_orders": False,
        }

    def _record_portfolio_risk(
        self,
        snapshot_type: str,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record risk after a paper workflow without changing its success semantics."""
        try:
            plan = plan or self._latest_plan()
            account = self.paper.snapshot()
            center = self._compose_portfolio_center(plan, account)
            if center is None:
                return {"state": "unavailable", "message": "当前计划没有Portfolio Risk证据"}
            record = self.portfolio_store.save_account_snapshot(
                center["target_portfolio"],
                center["risk_assessment"],
                account["positions"],
                center["exit_plan"],
                observed_at=str(account["asset_valuation"]["valued_at"]),
                snapshot_type=snapshot_type,
            )
            return {"state": "saved", **record}
        except Exception as exc:
            return {"state": "failed", "message": str(exc)[:300]}

    def _dashboard_unlocked(self) -> dict[str, Any]:
        """Build the current research and paper-account dashboard payload."""
        try:
            plan = self._latest_plan()
        except RuntimeError:
            plan = None
        preview = self._latest_preview()
        status = None
        if self._status_path().exists():
            status = json.loads(self._status_path().read_text(encoding="utf-8"))
        enabled = bool(self.raw["paper_account"].get("enabled")) and bool(
            self.raw["paper_account"].get("automatic_execution")
        )
        plan_after_close = False
        if plan and plan.get("generated_at"):
            generated = datetime.fromisoformat(str(plan["generated_at"]))
            generated = generated.astimezone(SHANGHAI_TZ) if generated.tzinfo else generated.replace(tzinfo=SHANGHAI_TZ)
            plan_after_close = generated.time() >= time(15, 10)
        now = self._now()
        preview_state = "none"
        if preview:
            preview_state = "current" if preview.get("research_date") == now.date().isoformat() else "stale"
        plan_state = "none"
        execution_ready = False
        if plan and plan.get("execution_ready") and plan_after_close:
            try:
                research_day = str(plan["research_date"])
                future_days = sorted(day for day in self._trading_days() if day > research_day)
                next_day = future_days[0] if future_days else None
                if now.date().isoformat() == research_day:
                    plan_state = "awaiting_next_session"
                elif next_day and now.date().isoformat() == next_day:
                    configured = time.fromisoformat(str(self.raw["paper_account"]["execute_time"]))
                    start = datetime.combine(now.date(), configured, tzinfo=SHANGHAI_TZ)
                    end = start + timedelta(minutes=int(self.raw["paper_account"].get("execution_window_minutes", 10)))
                    if now < start:
                        plan_state = "awaiting_execution_window"
                    elif now <= end:
                        plan_state = "execution_window_open"
                        execution_ready = True
                    else:
                        plan_state = "expired"
                else:
                    plan_state = "expired"
            except Exception:
                plan_state = "calendar_unavailable"
        account = self.paper.snapshot()
        portfolio_center = self._compose_portfolio_center(plan, account)
        return {
            "version": "1.0.0", "paper_automation_enabled": enabled,
            "can_submit_paper_orders": bool(self.raw["paper_account"].get("enabled")),
            "quote_feed": "polling_snapshot",
            "quote_refresh_seconds": int(self.raw["paper_account"].get("quote_refresh_seconds", 5)),
            "live_trading_enabled": False, "can_submit_orders": False,
            "latest_research": plan, "latest_preview": preview,
            "preview_state": preview_state, "collection_status": status,
            "automation_execution_ready": execution_ready,
            "plan_state": plan_state,
            "account": account,
            "asset_valuation": account["asset_valuation"],
            "portfolio_risk_center": portfolio_center,
            "activity": self.paper.activity(50),
            "production_backtest": point_in_time_dataset_status(self.root),
            "last_action": self._last_action, "last_error": self._last_error,
            "schedule": {
                "research": "交易日15:10", "execute": "下一交易日09:35",
                "monitor": "交易日09:35–14:55每5分钟", "preview": "交易日内手动刷新",
            },
        }

    def set_kill_switch(self, enabled: bool) -> None:
        """Serialize safety mutations with all paper-account workflows."""
        with self._lock:
            self.paper.set_kill_switch(enabled)

    def close(self) -> None:
        self.paper.close()
        close = getattr(getattr(self.client, "client", None), "close", None)
        if callable(close):
            close()
