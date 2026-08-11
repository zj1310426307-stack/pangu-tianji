from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..ai_copilot.evidence_reader import EvidenceReaderError
from .mobile_journal_store import MobileJournalStore


MOBILE_INVESTMENT_SERVICE_VERSION = "mobile-investment-assistant-v1.0.0"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_INTENT_REPORT_TYPES = {
    "stock_analysis": "stock_analysis",
    "why_selected": "stock_analysis",
    "portfolio_analysis": "portfolio_analysis",
    "risk_consultation": "risk_alert",
    "daily_review": "close_review",
}


class MobileInvestmentServiceError(RuntimeError):
    """Report a mobile composition failure without crossing into trading state."""


class MobileInvestmentService:
    """Compose mobile-first reads from existing evidence and valuation services."""

    def __init__(
        self,
        project_root: Path,
        *,
        daily_service: Any,
        workbench_service: Any,
        copilot_service: Any,
        investment_os_service: Any,
        journal_store: MobileJournalStore | None = None,
    ) -> None:
        """Retain read-only upstream services and one non-executable journal store."""
        self.root = Path(project_root).resolve()
        self.daily = daily_service
        self.workbench = workbench_service
        self.copilot = copilot_service
        self.investment_os = investment_os_service
        self.journal = journal_store or MobileJournalStore(
            self.root / "output" / "mobile_assistant.db"
        )
        self._owns_journal = journal_store is None

    def close(self) -> None:
        """Release only the project-owned mobile journal database."""
        if self._owns_journal:
            self.journal.close()

    def dashboard(self) -> dict[str, Any]:
        """Return a phone-sized dashboard with canonical server-side valuation data."""
        workbench = self.workbench.get()
        research = self.daily.dashboard()
        formal = self._formal_research(research)
        trade_date = datetime.now(SHANGHAI_TZ).date().isoformat()
        valuation = dict(workbench.get("asset_valuation") or {})
        daily_performance = self._daily_performance(workbench, trade_date)
        portfolio_center = dict(research.get("portfolio_risk_center") or {})
        target = dict(portfolio_center.get("target_portfolio") or {})
        risk_source = {
            "flags": list(workbench.get("risk_flags") or []),
            "assessment": portfolio_center.get("risk_assessment"),
            "exit_plan": portfolio_center.get("exit_plan"),
        }
        risk = self._risk_summary(risk_source, valuation)
        notifications = list(
            self.investment_os.notification_items(30).get("items") or []
        )
        reports = list(self.investment_os.reports(None, 20).get("items") or [])
        formal_candidates = list(formal.get("candidates") or [])
        opportunities = [
            {
                key: row.get(key)
                for key in (
                    "rank", "symbol", "name", "score", "industry", "last_price", "risk_tags"
                )
                if key in row
            }
            for row in formal_candidates[:10]
            if isinstance(row, Mapping)
        ]
        current_ratio = self._optional_number(valuation.get("exposure_ratio"))
        target_ratio = self._optional_number(target.get("target_exposure"))
        current_pct = round(current_ratio * 100.0, 4) if current_ratio is not None else None
        target_pct = round(target_ratio * 100.0, 4) if target_ratio is not None else None
        return {
            "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "trade_date": trade_date,
            # This object is copied byte-for-field from ValuationService output;
            # mobile clients must not recompute cash, equity, PnL or drawdown.
            "asset_valuation": valuation,
            "daily_performance": daily_performance,
            "allocation": {
                "current_exposure_ratio": valuation.get("exposure_ratio"),
                "target_exposure_ratio": target.get("target_exposure"),
                "current_exposure_pct": current_pct,
                "target_exposure_pct": target_pct,
                "current_exposure_display": (
                    f"{current_pct:.1f}%" if current_pct is not None else "暂无"
                ),
                "target_exposure_display": (
                    f"{target_pct:.1f}%" if target_pct is not None else "暂无"
                ),
                "cash_ratio": valuation.get("cash_ratio"),
                "position_count": valuation.get("position_count"),
                "availability": (
                    "available"
                    if valuation.get("exposure_ratio") is not None
                    else "unavailable"
                ),
                "source": valuation.get("service_version"),
            },
            "market": self._market_summary({
                "scope": "research_pool",
                "label": "研究池状态（非全市场指数）",
                "plan_state": research.get("plan_state"),
                "preview_state": research.get("preview_state"),
                "quote_feed": research.get("quote_feed"),
                "research_date": formal.get("research_date"),
            }),
            "risk": risk,
            "ai_summary": self._latest_ai_summary({"recent_reports": reports}),
            "opportunities": opportunities,
            "opportunities_availability": (
                "available" if formal.get("run_id") else "unavailable"
            ),
            "data_gaps": (
                []
                if formal.get("run_id")
                else ["尚无正式收盘研究run_id；未将盘中预览候选作为正式机会"]
            ),
            "notification_summary": {
                "unread_count": sum(item.get("status") == "unread" for item in notifications),
                "critical_count": sum(
                    item.get("status") == "unread" and item.get("level") == "CRITICAL"
                    for item in notifications
                ),
                "latest": notifications[:5],
            },
            "workflow_state": {
                "enabled": bool(getattr(self.investment_os.registry, "enabled", False)),
                "latest_report_time": reports[0].get("created_time") if reports else None,
                "timezone": "Asia/Shanghai",
            },
            "store_counts": self.journal.counts(),
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def portfolio(self) -> dict[str, Any]:
        """Return held positions with saved risk and exit evidence, never an order action."""
        operating = self.investment_os.dashboard()
        portfolio = dict(operating.get("portfolio") or {})
        risk = dict(operating.get("risk") or {})
        assessment = dict(risk.get("assessment") or {})
        exit_plan = dict(risk.get("exit_plan") or {})
        risk_by_symbol = {
            str(item.get("symbol")): item
            for item in assessment.get("security_risks") or []
            if isinstance(item, Mapping)
        }
        exits_by_symbol = {
            str(item.get("symbol")): item
            for item in exit_plan.get("signals") or []
            if isinstance(item, Mapping)
        }
        positions: list[dict[str, Any]] = []
        for position in portfolio.get("positions") or []:
            if not isinstance(position, Mapping):
                continue
            symbol = str(position.get("symbol") or "")
            positions.append({
                **dict(position),
                "security_risk": risk_by_symbol.get(symbol),
                "exit_signal": exits_by_symbol.get(symbol),
            })
        return {
            "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "asset_valuation": dict(operating.get("asset_valuation") or {}),
            "positions": positions,
            "target_portfolio": portfolio.get("target_portfolio"),
            "risk": self._risk_summary(
                risk, dict(operating.get("asset_valuation") or {})
            ),
            "exit_plan": exit_plan or None,
            "performance": dict(portfolio.get("performance") or {}),
            "attribution": list(portfolio.get("attribution") or []),
            "data_gaps": self._portfolio_gaps(assessment, exit_plan),
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def stock_detail(self, symbol: str) -> dict[str, Any]:
        """Build one stock page only from the latest formal-close run and held ledger."""
        normalized_symbol = symbol.strip().upper()
        formal = self._formal_research()
        workbench = self.workbench.get()
        holding = next(
            (
                dict(item)
                for item in workbench.get("positions") or []
                if str(item.get("symbol")) == normalized_symbol
            ),
            None,
        )
        if not formal["run_id"]:
            return self._unavailable_stock(
                normalized_symbol,
                holding,
                "尚无可用的正式收盘研究run_id；禁止回退盘中预览",
            )
        try:
            evidence = self.copilot.evidence(
                run_id=formal["run_id"],
                report_type="stock_analysis",
                symbol=normalized_symbol,
            )
        except (EvidenceReaderError, RuntimeError, ValueError) as exc:
            return self._unavailable_stock(normalized_symbol, holding, str(exc))
        items = list(evidence.get("evidence_items") or [])
        ranking = self._evidence_payload(items, f"E-DC-RANK-{normalized_symbol}")
        factors = self._evidence_payload(items, f"E-DC-FACTOR-{normalized_symbol}")
        target = self._evidence_payload(items, f"E-PR-POSITION-{normalized_symbol}")
        exit_signal = self._evidence_payload(items, f"E-PR-EXIT-{normalized_symbol}")
        security_risk = self._security_risk(items, normalized_symbol)
        report = self._latest_stock_report(normalized_symbol, formal["run_id"])
        return {
            "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": normalized_symbol,
            "name": (ranking or {}).get("name") or (holding or {}).get("name"),
            "availability": "available" if ranking else "unavailable",
            "formal_run_id": formal["run_id"],
            "research_date": formal["research_date"],
            "ranking": ranking,
            "factor_analysis": factors,
            "risk_analysis": security_risk,
            "target_position": target,
            "current_holding": holding,
            "exit_signal": exit_signal,
            "ai_explanation": report,
            "evidence": [
                {
                    "evidence_id": item.get("evidence_id"),
                    "source": item.get("source"),
                    "observed_at": item.get("observed_at"),
                }
                for item in items
                if isinstance(item, Mapping)
            ],
            "evidence_hash": evidence.get("evidence_hash"),
            "data_gaps": list(evidence.get("data_gaps") or []),
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def chat(
        self,
        *,
        intent: str,
        question: str,
        source_device_id: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Route a bounded mobile question to an evidence-grounded existing agent."""
        report_type = _INTENT_REPORT_TYPES.get(intent)
        if not report_type:
            raise MobileInvestmentServiceError("不支持的移动AI问题类型")
        normalized_symbol = symbol.strip().upper() if symbol else None
        if report_type == "stock_analysis" and not normalized_symbol:
            raise MobileInvestmentServiceError("股票分析必须指定股票代码")
        formal = self._formal_research()
        if not formal["run_id"]:
            return {
                "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "availability": "unavailable",
                "intent": intent,
                "question": question,
                "symbol": normalized_symbol,
                "report_id": None,
                "run_id": None,
                "answer": {},
                "evidence_ids": [],
                "evidence_hash": None,
                "data_gaps": ["尚无正式收盘研究run_id；未调用AI且未回退盘中预览"],
                "safety": self._safety(),
                "used_for_execution": False,
                "can_affect_execution": False,
                "can_trade": False,
                "can_create_orders": False,
            }
        report = self.copilot.generate(
            report_type=report_type,
            run_id=formal["run_id"],
            symbol=normalized_symbol,
            trigger="user_action",
        )
        audit = self.journal.add_chat(
            source_device_id=source_device_id,
            intent=intent,
            question=question,
            report=report,
        )
        return {
            "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "availability": "available",
            "intent": intent,
            "question": audit["question"],
            "symbol": normalized_symbol,
            "chat_id": audit["chat_id"],
            "report_id": report.get("report_id"),
            "run_id": report.get("run_id"),
            "answer": dict(report.get("content") or {}),
            "evidence_ids": list(report.get("evidence_ids") or []),
            "evidence_hash": report.get("evidence_hash"),
            "evaluation": report.get("evaluation"),
            "data_gaps": [],
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def chat_history(self, limit: int = 50) -> dict[str, Any]:
        """List prior mobile Copilot interactions without repeating model calls."""
        items = self.journal.list_chats(limit)
        read_report = getattr(self.copilot, "report", None)
        enriched: list[dict[str, Any]] = []
        for item in items:
            result = dict(item)
            result["answer"] = None
            result["answer_availability"] = "unavailable"
            report_id = str(item.get("report_id") or "")
            if report_id and callable(read_report):
                try:
                    report = read_report(report_id)
                    result["answer"] = dict(report.get("content") or {})
                    result["evaluation"] = report.get("evaluation")
                    result["answer_availability"] = "available"
                except (RuntimeError, ValueError, KeyError):
                    # History reads must remain usable when an old report is unavailable.
                    result["answer_availability"] = "unavailable"
            enriched.append(result)
        return {
            "items": enriched,
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def reports(self, report_type: str | None, limit: int) -> dict[str, Any]:
        """Expose Daily Investment OS reports through the mobile auth boundary."""
        result = dict(self.investment_os.reports(report_type, limit))
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        result["can_trade"] = False
        result["can_create_orders"] = False
        return result

    def report(self, report_id: str) -> dict[str, Any]:
        """Return one immutable operating report with evidence references."""
        result = dict(self.investment_os.report(report_id))
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        result["can_trade"] = False
        result["can_create_orders"] = False
        return result

    def notifications(self, limit: int) -> dict[str, Any]:
        """List the existing audited operating notifications."""
        result = dict(self.investment_os.notification_items(limit))
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        result["can_trade"] = False
        result["can_create_orders"] = False
        return result

    def mark_notification_read(self, notification_id: str) -> dict[str, Any]:
        """Acknowledge an in-app message without changing investment state."""
        result = dict(self.investment_os.mark_notification_read(notification_id))
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        result["can_trade"] = False
        result["can_create_orders"] = False
        return result

    def journal_entries(
        self,
        *,
        limit: int,
        entry_type: str | None,
        symbol: str | None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List user-authored investment notes without using them as strategy input."""
        today = datetime.now(SHANGHAI_TZ).date().isoformat()
        return {
            "items": self.journal.list_entries(
                limit=limit,
                entry_type=entry_type,
                symbol=symbol,
                include_archived=include_archived,
            ),
            "counts": self.journal.journal_statistics(today),
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    def create_journal_entry(
        self,
        *,
        source_device_id: str,
        idempotency_key: str,
        entry_type: str,
        trade_date: str,
        title: str,
        content: str,
        symbol: str | None,
        linked_report_id: str | None,
        review_due_date: str | None,
    ) -> dict[str, Any]:
        """Save one explicit user note that cannot affect research or execution."""
        result = self.journal.add_entry(
            source_device_id=source_device_id,
            idempotency_key=idempotency_key,
            entry_type=entry_type,
            trade_date=trade_date,
            title=title,
            content=content,
            symbol=symbol,
            linked_report_id=linked_report_id,
            review_due_date=review_due_date,
        )
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        return result

    def journal_entry(self, entry_id: str) -> dict[str, Any]:
        """Return one journal note and its revision audit without execution hooks."""
        result = self.journal.get_entry(entry_id)
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        return result

    def update_journal_entry(
        self,
        *,
        entry_id: str,
        source_device_id: str,
        idempotency_key: str,
        expected_version: int,
        title: str | None,
        content: str | None,
        review_due_date: str | None,
    ) -> dict[str, Any]:
        """Revise a note through optimistic locking while preserving its audit trail."""
        result = self.journal.update_entry(
            entry_id=entry_id,
            source_device_id=source_device_id,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            title=title,
            content=content,
            review_due_date=review_due_date,
        )
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        return result

    def archive_journal_entry(
        self,
        *,
        entry_id: str,
        source_device_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Soft-delete a note so mobile deletion remains reversible and auditable."""
        result = self.journal.archive_entry(
            entry_id=entry_id,
            source_device_id=source_device_id,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
        result["safety"] = self._safety()
        result["used_for_execution"] = False
        result["can_affect_execution"] = False
        return result

    def _formal_research(
        self, dashboard: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Select only the latest formal close run and never an intraday preview run."""
        source = dashboard if dashboard is not None else self.daily.dashboard()
        formal = source.get("latest_research") or {}
        mode = formal.get("mode")
        run_id = str(formal.get("run_id") or "")
        if mode != "formal_close_plan":
            run_id = ""
        return {
            "run_id": run_id or None,
            "research_date": str(formal.get("research_date") or "") or None,
            "candidates": list(formal.get("candidates") or []) if run_id else [],
        }

    @staticmethod
    def _daily_performance(workbench: Mapping[str, Any], trade_date: str) -> dict[str, Any]:
        """Expose today's persisted NAV return only when its date exactly matches today."""
        points = [
            item
            for item in workbench.get("nav") or []
            if isinstance(item, Mapping) and str(item.get("trade_date")) == trade_date
        ]
        if not points:
            return {
                "availability": "unavailable",
                "trade_date": trade_date,
                "daily_return": None,
                "daily_pnl": None,
                "source": "paper_nav",
                "message": "当日持久化NAV尚不可用，未使用累计收益冒充今日收益",
            }
        latest = points[-1]
        return {
            "availability": "available",
            "trade_date": trade_date,
            "daily_return": latest.get("daily_return"),
            "daily_pnl": None,
            "source": "paper_nav",
            "message": "今日收益率来自同交易日持久化NAV；当前无独立日内PnL字段",
        }

    @staticmethod
    def _market_summary(market: Mapping[str, Any]) -> dict[str, Any]:
        """Return an explicit market regime only when upstream evidence supplies one."""
        regime = market.get("regime") or market.get("market_status")
        if regime is None:
            return {
                **dict(market),
                "availability": "unavailable",
                "status": None,
                "message": "当前无Market Regime点时证据，不推断牛市、震荡或熊市",
            }
        return {
            **dict(market),
            "availability": "available",
            "status": regime,
            "message": "市场状态来自上游点时证据",
        }

    @staticmethod
    def _risk_summary(risk: Mapping[str, Any], valuation: Mapping[str, Any]) -> dict[str, Any]:
        """Shape saved risk evidence while taking drawdown only from ValuationService."""
        assessment = dict(risk.get("assessment") or {})
        score = assessment.get("weighted_security_risk")
        industry_exposure = dict(assessment.get("industry_exposure") or {})
        style_exposure = dict(assessment.get("style_exposure") or {})
        return {
            "availability": "available" if score is not None else "unavailable",
            "score": score,
            "risk_score": score,
            "current_drawdown": valuation.get("drawdown"),
            "max_drawdown": valuation.get("max_drawdown"),
            "drawdown_source": valuation.get("service_version"),
            "predicted_max_drawdown": assessment.get("predicted_max_drawdown"),
            "industry_exposure": industry_exposure,
            "industry_exposure_items": MobileInvestmentService._exposure_items(
                industry_exposure
            ),
            "style_exposure": style_exposure,
            "style_exposure_items": MobileInvestmentService._exposure_items(
                style_exposure
            ),
            "concentration": assessment.get("concentration") or {},
            "flags": list(risk.get("flags") or assessment.get("risk_flags") or []),
            "exit_plan": risk.get("exit_plan"),
        }

    @staticmethod
    def _latest_ai_summary(operating: Mapping[str, Any]) -> dict[str, Any]:
        """Reuse the newest published operating report instead of calling AI on a GET."""
        reports = [
            item
            for item in operating.get("recent_reports") or []
            if isinstance(item, Mapping)
        ]
        if not reports:
            return {
                "availability": "unavailable",
                "text": "尚无Daily Investment OS报告",
                "source_report_id": None,
                "generated_at": None,
            }
        latest = reports[0]
        content = dict(latest.get("content") or {})
        return {
            "availability": "available",
            "text": content.get("summary"),
            "guidance": list(content.get("operating_guidance") or []),
            "source_report_id": latest.get("report_id"),
            "generated_at": latest.get("created_time"),
            "ai_state": (content.get("ai_analysis") or {}).get("state"),
        }

    def _latest_stock_report(self, symbol: str, run_id: str) -> dict[str, Any] | None:
        """Find a saved stock explanation without generating a report during a GET."""
        reports = self.copilot.reports(100).get("items") or []
        for item in reports:
            if (
                item.get("report_type") == "stock_analysis"
                and item.get("subject_symbol") == symbol
                and item.get("run_id") == run_id
                and item.get("status") == "published"
            ):
                return {
                    "report_id": item.get("report_id"),
                    "content": item.get("content"),
                    "evidence_ids": item.get("evidence_ids"),
                    "evidence_hash": item.get("evidence_hash"),
                    "created_time": item.get("created_time"),
                    "used_for_execution": False,
                }
        return None

    @staticmethod
    def _evidence_payload(items: list[Any], evidence_id: str) -> dict[str, Any] | None:
        """Extract one already-bounded Evidence Reader payload by citation id."""
        for item in items:
            if isinstance(item, Mapping) and item.get("evidence_id") == evidence_id:
                return dict(item.get("payload") or {})
        return None

    @staticmethod
    def _security_risk(items: list[Any], symbol: str) -> dict[str, Any] | None:
        """Find one security risk record inside the bounded portfolio risk evidence."""
        for item in items:
            if not isinstance(item, Mapping) or not str(item.get("evidence_id", "")).startswith("E-PR-RISK-"):
                continue
            for risk in (item.get("payload") or {}).get("security_risks") or []:
                if isinstance(risk, Mapping) and str(risk.get("symbol")) == symbol:
                    return dict(risk)
        return None

    def _unavailable_stock(
        self,
        symbol: str,
        holding: dict[str, Any] | None,
        reason: str,
    ) -> dict[str, Any]:
        """Return an honest partial stock page when formal research evidence is absent."""
        return {
            "service_version": MOBILE_INVESTMENT_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "name": (holding or {}).get("name"),
            "availability": "unavailable",
            "formal_run_id": None,
            "research_date": None,
            "ranking": None,
            "factor_analysis": None,
            "risk_analysis": None,
            "target_position": None,
            "current_holding": holding,
            "exit_signal": None,
            "ai_explanation": None,
            "evidence": [],
            "evidence_hash": None,
            "data_gaps": [reason],
            "safety": self._safety(),
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _portfolio_gaps(
        assessment: Mapping[str, Any], exit_plan: Mapping[str, Any]
    ) -> list[str]:
        """Describe missing portfolio evidence instead of synthesizing values."""
        gaps: list[str] = []
        if not assessment:
            gaps.append("尚无Portfolio Risk Center风险快照")
        if not exit_plan:
            gaps.append("尚无Exit Engine退出信号快照")
        return gaps

    @staticmethod
    def _safety() -> dict[str, bool]:
        """Return the immutable mobile non-trading capability boundary."""
        return {
            "live_trading_enabled": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_access_broker_credentials": False,
        }

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        """Return a finite numeric display input without inventing missing values."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and number not in {float("inf"), float("-inf")} else None

    @staticmethod
    def _exposure_items(values: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Convert evidence ratios to server-owned display percentages for mobile charts."""
        items: list[dict[str, Any]] = []
        for label, value in values.items():
            ratio = MobileInvestmentService._optional_number(value)
            if ratio is None:
                continue
            percent = round(ratio * 100.0, 4)
            items.append({
                "label": str(label),
                "value": ratio,
                "percent": percent,
                "display": f"{percent:.1f}%",
            })
        return sorted(items, key=lambda item: (-float(item["percent"]), item["label"]))
