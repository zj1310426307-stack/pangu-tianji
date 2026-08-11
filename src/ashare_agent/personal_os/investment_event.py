from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import content_hash, stable_id
from .store import PersonalOSStore


class InvestmentEventService:
    """Build an auditable life cycle from paper trades and explicit reflections."""

    def __init__(self, store: PersonalOSStore) -> None:
        self.store = store

    def sync_paper_trades(self, workbench: Mapping[str, Any]) -> dict[str, Any]:
        """Import completed MockBroker trades once; never submit or alter an order."""
        created = 0
        replayed = 0
        items: list[dict[str, Any]] = []
        trades = list((workbench.get("activity") or {}).get("trades") or [])
        for trade in trades:
            if not isinstance(trade, Mapping):
                continue
            side = str(trade.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            source_id = str(
                trade.get("trade_id")
                or trade.get("fill_id")
                or f"{trade.get('client_order_id')}:{trade.get('trade_date')}:{side}:"
                   f"{trade.get('quantity')}:{trade.get('price')}"
            )
            trade_date = str(trade.get("trade_date") or "")[:10]
            safe_payload = {
                key: trade.get(key)
                for key in (
                    "trade_id", "client_order_id", "trade_date", "symbol", "name",
                    "side", "quantity", "price", "gross", "fee", "tax", "slippage",
                    "realized_pnl", "reason",
                )
                if key in trade
            }
            ref = stable_id("E-POS-TRADE", source_id)
            saved, is_created = self.store.save_event({
                "idempotency_key": f"paper-trade:{source_id}",
                "event_type": side,
                "trade_date": trade_date,
                "symbol": trade.get("symbol"),
                "name": trade.get("name"),
                "reason": str(trade.get("reason") or "本地模拟成交同步"),
                "result_pnl": trade.get("realized_pnl") if side == "SELL" else None,
                "source_type": "paper_trade",
                "source_id": source_id,
                "evidence_ids": [ref],
                "source_hash": content_hash(safe_payload),
                "payload": safe_payload,
            })
            items.append(saved)
            created += int(is_created)
            replayed += int(not is_created)
        return {
            "created_count": created,
            "replayed_count": replayed,
            "items": items,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def create_manual(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Create OBSERVE/REVIEW/LEARN; BUY/SELL remain ledger-derived facts only."""
        event_type = str(values["event_type"]).upper()
        if event_type not in {"OBSERVE", "REVIEW", "LEARN"}:
            raise ValueError("手工事件只允许 OBSERVE、REVIEW 或 LEARN")
        source_id = str(values.get("source_id") or stable_id(
            "manual", str(values["idempotency_key"])
        ))
        source_payload = {
            "event_type": event_type,
            "trade_date": str(values["trade_date"]),
            "symbol": values.get("symbol"),
            "reason": str(values["reason"]),
        }
        saved, _ = self.store.save_event({
            "idempotency_key": str(values["idempotency_key"]),
            "event_type": event_type,
            "trade_date": str(values["trade_date"]),
            "symbol": values.get("symbol"),
            "name": values.get("name"),
            "reason": str(values["reason"]),
            "score": values.get("score"),
            "risk_score": values.get("risk_score"),
            "result_pnl": values.get("result_pnl"),
            "source_type": "user_record",
            "source_id": source_id,
            "evidence_ids": list(values.get("evidence_ids") or []),
            "source_hash": content_hash(source_payload),
            "payload": {"recorded_at": datetime.now(timezone.utc).isoformat()},
        })
        return saved

