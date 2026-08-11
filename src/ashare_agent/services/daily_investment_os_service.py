from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from ..ai_copilot.copilot_service import CopilotService
from ..core.contracts import InvestmentReportType, NotificationLevel
from ..scheduler.task_registry import TaskRegistry
from .daily_research_service import DailyResearchService
from .investment_report_center import InvestmentReportCenter
from .model_service import ModelService
from .notification_service import NotificationService
from .workbench_service import WorkbenchService


DAILY_INVESTMENT_OS_VERSION = "daily-investment-os-v1.0.0"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_COPILOT_REPORT = {
    "morning_report": "morning_report",
    "intraday_monitor": "risk_alert",
    "closing_review": "close_review",
    "weekly_report": "coach_review",
}


class DailyInvestmentOSService:
    """Build evidence-backed operating reports through read-only service calls."""

    def __init__(
        self,
        project_root: Path,
        daily_service: Any | None = None,
        workbench_service: Any | None = None,
        copilot_service: Any | None = None,
        report_center: InvestmentReportCenter | None = None,
        notification_service: NotificationService | None = None,
        registry: TaskRegistry | None = None,
    ) -> None:
        self.root = Path(project_root).resolve()
        settings_path = self.root / "config" / "settings.yaml"
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        self.config = dict(raw.get("daily_investment_os") or {})
        self._owned_daily = daily_service is None
        self.daily = daily_service or DailyResearchService(self.root)
        self._owned_models = copilot_service is None
        self.models = ModelService() if self._owned_models else None
        self.copilot = copilot_service or CopilotService(self.root, self.models)
        if workbench_service is None:
            self.workbench = WorkbenchService(
                self.daily.paper,
                self.daily.raw["paper_account"],
                self.models or self.copilot.model_service,
                self.daily.paper_lock,
                getattr(self.daily, "review_valuation", None),
                getattr(self.daily, "valuation_service", None),
            )
        else:
            self.workbench = workbench_service
        self.center = report_center or InvestmentReportCenter(
            self.root / "output" / "daily_investment_os.db"
        )
        self.notifications = notification_service or NotificationService(
            self.center, self.config
        )
        self.registry = registry or TaskRegistry(self.config)

    def close(self) -> None:
        """Release only resources created by this service."""
        if self._owned_daily:
            self.daily.close()

    def generate_report(
        self,
        report_type: str,
        trade_date: str | None = None,
        scheduled_for: str | None = None,
    ) -> dict[str, Any]:
        """Generate one non-executable report from saved evidence and account reads."""
        selected = InvestmentReportType(report_type).value
        now = datetime.now(SHANGHAI_TZ)
        selected_date = trade_date or now.date().isoformat()
        research = self.daily.dashboard()
        formal = research.get("latest_research") or {}
        formal_run_id = (
            str(formal.get("run_id") or "")
            if formal.get("mode") in {None, "formal_close_plan"}
            else ""
        )
        evidence = self.copilot.evidence(
            run_id=formal_run_id or None,
            report_type=_COPILOT_REPORT[selected],
        )
        run_id = str(evidence["run_id"])
        workbench = self.workbench.get()
        snapshot = self._snapshot(workbench, research)
        data_gaps = list(evidence.get("data_gaps") or [])
        content = self._deterministic_content(
            selected,
            selected_date,
            scheduled_for,
            snapshot,
            data_gaps,
        )
        status = "published"
        try:
            ai_report = self.copilot.generate(
                report_type=_COPILOT_REPORT[selected],
                run_id=run_id,
                trigger="daily_os",
            )
            content["ai_analysis"] = {
                "state": "published",
                "report_id": ai_report.get("report_id"),
                "content": ai_report.get("content", {}),
                "evaluation": ai_report.get("evaluation"),
                "used_for_execution": False,
            }
        except Exception as exc:
            status = "degraded"
            content["ai_analysis"] = {
                "state": "unavailable",
                "message": f"{type(exc).__name__}: AI解读不可用，已保留确定性证据报告",
                "used_for_execution": False,
            }
            content["data_gaps"].append("AI模型解读未发布，不影响确定性数据与风险提示")

        refs = self._evidence_references(evidence, snapshot)
        saved = self.center.save_report(
            report_type=selected,
            trade_date=selected_date,
            run_id=run_id,
            portfolio_id=self._portfolio_id(research),
            agent_version=DAILY_INVESTMENT_OS_VERSION,
            content=content,
            evidence=refs,
            status=status,
        )
        level = self._notification_level(content, status)
        notification = self.notifications.notify(
            level=level,
            title=content["title"],
            message=content["summary"],
            source_type="investment_report",
            source_id=saved["report_id"],
        )
        saved["notification"] = notification
        return saved

    def dashboard(self) -> dict[str, Any]:
        """Return the Investment Dashboard without starting reports or orders."""
        now = datetime.now(SHANGHAI_TZ)
        workbench = self.workbench.get()
        research = self.daily.dashboard()
        snapshot = self._snapshot(workbench, research)
        reports = self.center.list_reports(limit=20)
        notifications = self.center.list_notifications(limit=30)
        tasks = self.center.list_tasks(limit=30)
        latest = {
            report_type.value: self.center.latest_report(report_type.value)
            for report_type in InvestmentReportType
        }
        return {
            "service_version": DAILY_INVESTMENT_OS_VERSION,
            "generated_at": now.isoformat(),
            "trade_date": now.date().isoformat(),
            "workflow_state": self._workflow_state(now, reports),
            **snapshot,
            "latest_reports": latest,
            "recent_reports": reports,
            "notifications": notifications,
            "recent_tasks": tasks,
            "jobs": self.registry.definitions(),
            "counts": self.center.counts(),
            "safety": {
                "mode": "read_only_investment_operations",
                "live_trading_enabled": False,
                "can_trade": False,
                "can_create_orders": False,
                "can_modify_strategy": False,
                "can_modify_portfolio": False,
                "can_modify_risk": False,
            },
            "can_trade": False,
            "can_create_orders": False,
        }

    def reports(self, report_type: str | None = None, limit: int = 50) -> dict[str, Any]:
        """List persisted reports without generation side effects."""
        if report_type is not None:
            InvestmentReportType(report_type)
        return {
            "items": self.center.list_reports(report_type=report_type, limit=limit),
            "can_trade": False,
            "can_create_orders": False,
        }

    def report(self, report_id: str) -> dict[str, Any]:
        """Read one immutable operating report."""
        return self.center.report(report_id)

    def notification_items(self, limit: int = 100) -> dict[str, Any]:
        """List local operating notifications without delivery side effects."""
        return {
            "items": self.center.list_notifications(limit=limit),
            "can_trade": False,
            "can_create_orders": False,
        }

    def mark_notification_read(self, notification_id: str) -> dict[str, Any]:
        """Acknowledge one in-app notification only."""
        return self.center.mark_notification_read(notification_id)

    @staticmethod
    def _snapshot(workbench: Mapping[str, Any], research: Mapping[str, Any]) -> dict[str, Any]:
        valuation = dict(workbench.get("asset_valuation") or {})
        candidates = list((research.get("latest_research") or {}).get("candidates") or [])
        if not candidates:
            candidates = list((research.get("latest_preview") or {}).get("candidates") or [])
        opportunities = [
            {
                key: row.get(key)
                for key in ("rank", "symbol", "name", "score", "industry", "last_price", "risk_tags")
                if key in row
            }
            for row in candidates[:10]
            if isinstance(row, Mapping)
        ]
        portfolio_center = research.get("portfolio_risk_center") or {}
        return {
            "asset_valuation": valuation,
            "portfolio": {
                "positions": list(workbench.get("positions") or []),
                "target_portfolio": portfolio_center.get("target_portfolio"),
                "activity_summary": dict(workbench.get("activity_summary") or {}),
                "performance": dict(workbench.get("performance") or {}),
                "attribution": list(workbench.get("symbol_attribution") or []),
                "discipline": dict(workbench.get("discipline") or {}),
                "recent_activity": {
                    key: list((workbench.get("activity") or {}).get(key) or [])[:20]
                    for key in ("orders", "trades", "events")
                },
                "valuation_source": dict(workbench.get("valuation") or {}),
            },
            "risk": {
                "flags": list(workbench.get("risk_flags") or []),
                "assessment": portfolio_center.get("risk_assessment"),
                "exit_plan": portfolio_center.get("exit_plan"),
            },
            "market": {
                "scope": "research_pool",
                "label": "研究池状态（非全市场指数）",
                "plan_state": research.get("plan_state"),
                "preview_state": research.get("preview_state"),
                "quote_feed": research.get("quote_feed"),
                "research_date": (research.get("latest_research") or {}).get("research_date"),
                "generated_at": (research.get("latest_research") or {}).get("generated_at"),
            },
            "opportunities": opportunities,
        }

    def _deterministic_content(
        self,
        report_type: str,
        trade_date: str,
        scheduled_for: str | None,
        snapshot: dict[str, Any],
        data_gaps: list[str],
    ) -> dict[str, Any]:
        labels = {
            "morning_report": "盘古晨报",
            "intraday_monitor": "盘中风险监控",
            "closing_review": "盘后投资复盘",
            "weekly_report": "盘古周报",
        }
        valuation = snapshot["asset_valuation"]
        positions = snapshot["portfolio"]["positions"]
        risk_flags = snapshot["risk"]["flags"]
        active_risk_flags = [item for item in risk_flags if item.get("level") != "success"]
        critical = [item for item in risk_flags if item.get("level") == "danger"]
        summary = (
            f"模拟账户权益{float(valuation.get('equity') or 0):,.2f}元，"
            f"持仓{len(positions)}只，当前风险提示{len(active_risk_flags)}条"
        )
        content: dict[str, Any] = {
            "report_type": report_type,
            "title": f"{trade_date} {labels[report_type]}",
            "summary": summary,
            "trade_date": trade_date,
            "scheduled_for": scheduled_for,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **snapshot,
            "data_gaps": list(dict.fromkeys(data_gaps)),
            "operating_guidance": self._guidance(report_type, critical, positions),
            "safety": {
                "research_only": True,
                "can_trade": False,
                "can_create_orders": False,
                "used_for_execution": False,
            },
        }
        previous = self.center.latest_report(report_type)
        content["change_since_previous"] = self._change(previous, valuation)
        if report_type == "morning_report":
            target_portfolio = snapshot["portfolio"].get("target_portfolio") or {}
            target_exposure = target_portfolio.get("target_exposure")
            current_exposure = valuation.get("exposure_ratio")
            exposure_state = "evidence_unavailable"
            if target_exposure is not None and current_exposure is not None:
                exposure_state = (
                    "within_rebalance_band"
                    if abs(float(target_exposure) - float(current_exposure)) < 0.05
                    else "outside_rebalance_band"
                )
            content["morning_brief"] = {
                "market_environment": snapshot["market"],
                "position_guidance": {
                    "current_exposure_ratio": current_exposure,
                    "evidence_target_exposure": target_exposure,
                    "cash_ratio": valuation.get("cash_ratio"),
                    "position_count": len(positions),
                    "state": exposure_state,
                    "creates_order": False,
                },
                "opportunities": snapshot["opportunities"],
                "risks": risk_flags,
                "today_focus": content["operating_guidance"],
            }
        elif report_type == "intraday_monitor":
            content["intraday_observation"] = {
                "position_anomalies": [
                    item for item in risk_flags
                    if item.get("code") in {"PENDING_EXIT", "T1_LOCKED", "UNKNOWN_ORDER"}
                ],
                "market_anomalies": {
                    "state": "evidence_unavailable",
                    "message": "当前无全市场指数与赚钱效应点时证据",
                },
                "portfolio_risk_change": content["change_since_previous"],
                "alerts": risk_flags,
            }
            content["data_gaps"].append("尚无全市场盘中异常基准证据")
        elif report_type == "closing_review":
            activity = snapshot["portfolio"]["activity_summary"]
            content["closing_analysis"] = {
                "performance": snapshot["portfolio"]["performance"],
                "attribution": snapshot["portfolio"]["attribution"],
                "strategy_review": snapshot["portfolio"]["discipline"],
                "error_summary": {
                    "rejection_count": activity.get("rejection_count"),
                    "unknown_order_count": activity.get("unknown_order_count"),
                    "pnl_reconciliation_gap": valuation.get("pnl_reconciliation_gap"),
                },
                "next_session_focus": content["operating_guidance"],
            }
        elif report_type == "weekly_report":
            content["weekly_account_review"] = self._weekly_account_review(snapshot["portfolio"])
            content["weekly_review"] = {
                "return_analysis": content["weekly_account_review"],
                "strategy_performance": {"state": "evidence_unavailable"},
                "position_changes": {"state": "evidence_unavailable"},
                "investment_summary": content["operating_guidance"],
            }
            content["data_gaps"].extend(
                [
                    "尚无可验证的周度基准超额证据",
                    "尚无历史因子周表现证据",
                    "尚无可回放的周初/周末持仓变化快照",
                ]
            )
        content["data_gaps"] = list(dict.fromkeys(content["data_gaps"]))
        return content

    @staticmethod
    def _guidance(report_type: str, critical: list[dict[str, Any]], positions: list[dict[str, Any]]) -> list[str]:
        guidance = ["仅核对证据与风险，不自动下单或修改策略"]
        if critical:
            guidance.append("优先处理账户对账、退出队列或回撤风险告警")
        if report_type == "morning_report":
            guidance.append("开盘前检查持仓可卖数量、估值时效与当日研究证据")
        elif report_type == "intraday_monitor":
            guidance.append("盘中只监控估值、持仓与风险变化，不触发模拟撮合或止损卖出")
        elif report_type == "closing_review":
            guidance.append("盘后复核费用、已实现/未实现损益与下一交易日关注项")
        else:
            guidance.append("周度复盘仅总结模拟账户证据与纪律，不生成交易计划")
        if not positions:
            guidance.append("当前无模拟持仓，持仓风险部分为空")
        return guidance

    @staticmethod
    def _weekly_account_review(portfolio: Mapping[str, Any]) -> dict[str, Any]:
        performance = dict(portfolio.get("performance") or {})
        summary = dict(portfolio.get("activity_summary") or {})
        return {
            "net_pnl": performance.get("net_pnl"),
            "realized_pnl": performance.get("realized_pnl"),
            "unrealized_pnl": performance.get("unrealized_pnl"),
            "win_rate": performance.get("win_rate"),
            "turnover_ratio": performance.get("turnover_ratio"),
            "trade_count": summary.get("trade_count"),
            "total_fees": summary.get("total_fees"),
            "scope": "当前本地模拟账户累计证据",
        }

    @staticmethod
    def _change(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
        if not previous:
            return {"state": "unavailable", "message": "尚无同类上期报告"}
        prior = (previous.get("content") or {}).get("asset_valuation") or {}
        fields = ("equity", "pnl", "drawdown", "exposure_ratio")
        deltas: dict[str, float] = {}
        for field in fields:
            if current.get(field) is not None and prior.get(field) is not None:
                deltas[field] = round(float(current[field]) - float(prior[field]), 8)
        return {
            "state": "available" if deltas else "unavailable",
            "source_report_id": previous.get("report_id"),
            "deltas": deltas,
        }

    @staticmethod
    def _evidence_references(evidence: Mapping[str, Any], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}
        for item in evidence.get("evidence_items") or []:
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id:
                refs[evidence_id] = {
                    "evidence_id": evidence_id,
                    "source_type": str(item.get("source") or "copilot_evidence"),
                    "source_id": str(evidence.get("run_id") or "unknown"),
                    "observed_at": item.get("observed_at"),
                }
        valued_at = snapshot.get("asset_valuation", {}).get("valued_at")
        valuation_payload = snapshot.get("asset_valuation", {})
        valuation_hash = hashlib.sha256(
            json.dumps(
                valuation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()[:16]
        refs["E-OPS-VALUATION"] = {
            "evidence_id": "E-OPS-VALUATION",
            "source_type": "valuation_service",
            "source_id": (
                f"{valuation_payload.get('service_version', 'valuation')}:{valued_at}:{valuation_hash}"
            ),
            "observed_at": valued_at,
        }
        return list(refs.values())

    @staticmethod
    def _portfolio_id(research: Mapping[str, Any]) -> str | None:
        portfolio = (research.get("portfolio_risk_center") or {}).get("target_portfolio") or {}
        value = portfolio.get("portfolio_id")
        return str(value) if value else None

    @staticmethod
    def _notification_level(content: Mapping[str, Any], status: str) -> NotificationLevel:
        codes = {
            str(item.get("code") or "")
            for item in content.get("risk", {}).get("flags", [])
            if item.get("level") == "danger"
        }
        if codes & {"UNKNOWN_ORDER", "PNL_RECONCILIATION_GAP", "DRAWDOWN_NEAR_LIMIT"}:
            return NotificationLevel.CRITICAL
        if status == "degraded" or codes:
            return NotificationLevel.WARNING
        return NotificationLevel.INFO

    def _workflow_state(self, now: datetime, reports: list[Mapping[str, Any]]) -> dict[str, Any]:
        latest_time = reports[0].get("created_time") if reports else None
        due = [job.job_name for job, _slot in self.registry.due_jobs(now)]
        return {
            "enabled": self.registry.enabled,
            "state": "due" if due else "waiting",
            "due_jobs": due,
            "latest_report_time": latest_time,
            "timezone": "Asia/Shanghai",
        }
