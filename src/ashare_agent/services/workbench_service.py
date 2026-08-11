from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..paper_portfolio import PaperPortfolio
from .model_service import ModelService
from .valuation_service import ValuationService


class WorkbenchService:
    """Compose the review workspace from the persistent paper account only."""

    def __init__(
        self,
        paper: PaperPortfolio,
        paper_config: dict[str, Any],
        model_service: ModelService,
        paper_lock: Any,
        valuation_provider: Callable[[list[str]], dict[str, Any]] | None = None,
        valuation_service: ValuationService | None = None,
    ) -> None:
        """Store paper-ledger dependencies and an optional read-only quote source."""
        self.paper = paper
        self.paper_config = paper_config
        self.model_service = model_service
        self.paper_lock = paper_lock
        self.valuation_provider = valuation_provider
        self.valuation_service = valuation_service or paper.valuation_service

    def _valuation(self, symbols: list[str]) -> dict[str, Any]:
        """Fetch a short-lived holdings valuation without making review fragile.

        The live quote source is optional and failures degrade to the persisted
        paper NAV. A five-second cache matches the page quote cadence and avoids
        duplicate supplier calls when several read-only panels refresh together.
        """
        return self.valuation_service.market_prices(
            symbols, provider=self.valuation_provider
        )

    @staticmethod
    def _ratio_score(numerator: int, denominator: int) -> int | None:
        """Convert an evidence ratio to a bounded score when evidence exists."""
        if denominator <= 0:
            return None
        return max(0, min(100, round(100 * numerator / denominator)))

    @staticmethod
    def _dimension(
        key: str, label: str, score: int | None, evidence: str
    ) -> dict[str, Any]:
        """Create one paper-account discipline dimension with availability."""
        return {
            "key": key,
            "label": label,
            "score": score,
            "availability": "available" if score is not None else "unavailable",
            "evidence": evidence,
        }

    def _discipline(self, review: dict[str, Any]) -> dict[str, Any]:
        """Score paper execution evidence without evaluating stock quality."""
        activity = review["activity"]
        orders = activity["orders"]
        trades = activity["trades"]
        positions = review["positions"]
        has_evidence = bool(orders or trades or positions or activity["nav"])
        if not has_evidence:
            return {
                "score": None,
                "grade": "暂无模拟交易证据",
                "meaning": "仅衡量模拟账户执行与审计证据，不评价盈利能力",
                "dimensions": [],
            }

        order_ids = [str(item.get("client_order_id") or "") for item in orders]
        trade_ids = {str(item.get("client_order_id") or "") for item in trades}
        filled = [item for item in orders if item.get("status") == "FILLED"]
        within_limits = (
            len(positions) <= int(self.paper_config["max_positions"])
            and float(review["account"]["market_value"])
            <= float(review["account"]["equity"])
            * float(self.paper_config["max_total_exposure_pct"])
            + 1e-6
            and all(
                float(item["weight"])
                <= float(self.paper_config["target_position_pct"]) + 1e-6
                for item in positions
            )
        )
        t1_consistent = all(
            0 <= int(item["available_quantity"]) <= int(item["quantity"])
            for item in positions
        )
        dimensions = [
            self._dimension(
                "order_trace",
                "订单可追踪",
                self._ratio_score(sum(bool(item) for item in order_ids), len(order_ids)),
                f"{sum(bool(item) for item in order_ids)}/{len(order_ids)} 笔模拟订单包含唯一编号",
            ),
            self._dimension(
                "fill_link",
                "成交可对账",
                self._ratio_score(
                    sum(str(item.get("client_order_id")) in trade_ids for item in filled),
                    len(filled),
                ),
                f"{sum(str(item.get('client_order_id')) in trade_ids for item in filled)}/{len(filled)} 笔已成交订单存在成交记录",
            ),
            self._dimension(
                "round_lot",
                "整手纪律",
                self._ratio_score(
                    sum(
                        int(item.get("quantity") or 0) > 0
                        and (
                            int(item.get("quantity") or 0) % 100 == 0
                            or (
                                item.get("side") == "SELL"
                                and item.get("status") == "FILLED"
                            )
                        )
                        for item in orders
                    ),
                    len(orders),
                ),
                "买入按100股整手审计；卖出允许整仓零股尾数",
            ),
            self._dimension(
                "position_control",
                "仓位纪律",
                100 if within_limits else 0,
                (
                    f"当前{len(positions)}只持仓；"
                    f"单股{float(self.paper_config['target_position_pct']):.0%}、"
                    f"总仓位{float(self.paper_config['max_total_exposure_pct']):.0%}、"
                    f"最多{self.paper_config['max_positions']}只"
                ),
            ),
            self._dimension(
                "t1_control",
                "T+1纪律",
                100 if t1_consistent else 0,
                "所有持仓可卖数量均未超过总数量" if t1_consistent else "发现可卖数量异常",
            ),
            self._dimension(
                "cost_accounting",
                "费用记录",
                self._ratio_score(
                    sum(item.get("fee") is not None and float(item["fee"]) >= 0 for item in trades),
                    len(trades),
                ),
                f"{sum(item.get('fee') is not None and float(item['fee']) >= 0 for item in trades)}/{len(trades)} 笔成交记录费用",
            ),
        ]
        available = [item["score"] for item in dimensions if item["score"] is not None]
        score = round(sum(available) / len(available)) if available else None
        if score is None:
            grade = "暂无模拟交易证据"
        elif score >= 90:
            grade = "证据完整"
        elif score >= 75:
            grade = "证据良好"
        elif score >= 60:
            grade = "仍需补充"
        else:
            grade = "证据不足"
        return {
            "score": score,
            "grade": grade,
            "meaning": "仅衡量模拟账户执行与审计证据，不评价盈利能力",
            "dimensions": dimensions,
        }

    def get(self) -> dict[str, Any]:
        """Serialize recap reads with account writes on the shared connection."""
        with self.paper_lock:
            return self._get_unlocked()

    def _get_unlocked(self) -> dict[str, Any]:
        """Return a recap of stocks actually held or traded by MockBroker."""
        symbols = [str(item["symbol"]) for item in self.paper.positions()]
        valuation = self._valuation(symbols)
        review = self.paper.review(
            200,
            quotes={
                str(symbol): float(price)
                for symbol, price in valuation.get("prices", {}).items()
            },
            valuation={key: value for key, value in valuation.items() if key != "prices"},
        )
        activity = review["activity"]
        orders = activity["orders"]
        trades = activity["trades"]
        events = activity["events"]
        dates = [
            str(item.get(key))
            for group, key in (
                (orders, "trade_date"),
                (trades, "trade_date"),
                (events, "observed_at"),
            )
            for item in group
            if item.get(key)
        ]
        model = self.model_service.status()
        source_nav_date = review["source_nav_date"]
        availability = [
            {
                "key": "paper_ledger",
                "label": "模拟账户账本",
                "state": "available",
                "source": "paper_account.db",
                "message": "持仓、订单、成交、费用和净值均来自本地持久化模拟账户",
            },
            {
                "key": "valuation_snapshot",
                "label": "持仓估值快照",
                "state": (
                    "stale"
                    if review["valuation"].get("stale")
                    else "available"
                ),
                "source": str(review["valuation"].get("source") or "paper_nav"),
                "message": str(review["valuation"].get("message") or "估值来源未说明"),
            },
            {
                "key": "configured_whitelist",
                "label": "固定白名单",
                "state": "unavailable",
                "source": "not_used",
                "message": "复盘不读取固定ETF白名单，只读取模拟账户实际持仓与成交",
            },
            {
                "key": "model_explanation",
                "label": "模型解释",
                "state": "available" if model["state"] == "connected" else "unavailable",
                "source": model["provider"],
                "message": model["message"],
            },
        ]
        rejected = sum(
            item.get("status") in {"RISK_REJECTED", "REJECTED"} for item in orders
        )
        unknown = sum(item.get("status") == "UNKNOWN" for item in orders)
        account = review["account"]
        asset_valuation = review["asset_valuation"]
        flags: list[dict[str, str]] = []
        if review["valuation"].get("stale"):
            flags.append({
                "level": "warning",
                "code": "VALUATION_STALE",
                "message": "持仓实时估值不可用，当前复盘已回退至最近净值或含费成本。",
            })
        pending = [item for item in review["positions"] if item.get("pending_exit")]
        if pending:
            flags.append({
                "level": "danger",
                "code": "PENDING_EXIT",
                "message": f"{len(pending)}只持仓等待退出，请关注T+1、停牌或跌停约束。",
            })
        locked = [item for item in review["positions"] if int(item["available_quantity"]) <= 0]
        if locked:
            flags.append({
                "level": "warning",
                "code": "T1_LOCKED",
                "message": f"{len(locked)}只持仓当前无可卖数量。",
            })
        if float(asset_valuation["drawdown"]) <= -float(self.paper_config["max_drawdown_pct"]) * 0.8:
            flags.append({
                "level": "danger",
                "code": "DRAWDOWN_NEAR_LIMIT",
                "message": "组合回撤已接近或达到停止开仓线。",
            })
        if abs(float(asset_valuation["pnl_reconciliation_gap"])) > 0.01:
            flags.append({
                "level": "danger",
                "code": "PNL_RECONCILIATION_GAP",
                "message": "账户净盈亏与已实现、未实现盈亏无法对平，请停止新增模拟交易并检查账本。",
            })
        if unknown:
            flags.append({
                "level": "danger",
                "code": "UNKNOWN_ORDER",
                "message": f"存在{unknown}笔UNKNOWN订单，禁止自动重发。",
            })
        if not flags:
            flags.append({
                "level": "success",
                "code": "NO_ACTIVE_ALERT",
                "message": "当前未发现账本对账、T+1、退出队列或回撤告警。",
            })
        return {
            "version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "paper_portfolio_review",
            "source_nav_date": source_nav_date,
            "account": review["account"],
            "asset_valuation": asset_valuation,
            "positions": review["positions"],
            "nav": review["nav"],
            "equity_curve": review["equity_curve"],
            "performance": review["performance"],
            "fee_breakdown": review["fee_breakdown"],
            "symbol_attribution": review["symbol_attribution"],
            "valuation": review["valuation"],
            "risk_flags": flags,
            "activity": activity,
            "discipline": self._discipline(review),
            "activity_summary": {
                "position_count": len(review["positions"]),
                "order_count": len(orders),
                "trade_count": len(trades),
                "rejection_count": rejected,
                "unknown_order_count": unknown,
                "monitor_event_count": len(events),
                "total_fees": sum(float(item.get("fee") or 0) for item in trades),
                "realized_pnl": float(account["realized_pnl"]),
                "unrealized_pnl": float(account["unrealized_pnl"]),
                "turnover": float(account["turnover"]),
                "sell_trade_count": int(review["performance"]["sell_trade_count"]),
                "latest_activity_date": max(dates) if dates else None,
            },
            "data_availability": availability,
            "can_submit_orders": False,
            "provenance": "仅来自盘古·天机本地模拟账户；固定ETF白名单未参与复盘",
        }
