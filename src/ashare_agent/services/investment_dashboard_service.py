from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
INVESTMENT_DASHBOARD_VERSION = "investment-dashboard-v1.0.0"
DEFAULT_CACHE_SECONDS = 15


class InvestmentDashboardService:
    """Compose one bounded, read-only investment cockpit from existing services.

    The service does not calculate valuation, factor scores, portfolio risk or AI
    advice. It only reshapes authoritative backend evidence and caches that view
    briefly so one browser refresh cannot fan out into multiple inconsistent reads.
    """

    def __init__(
        self,
        *,
        workbench_service: Any,
        daily_research_service: Any,
        investment_os_service: Any,
        personal_os_service: Any,
        copilot_service: Any,
        cache_seconds: int = DEFAULT_CACHE_SECONDS,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        """Bind read-only collaborators and initialize the in-process snapshot."""
        self.workbench = workbench_service
        self.daily = daily_research_service
        self.investment_os = investment_os_service
        self.personal_os = personal_os_service
        self.copilot = copilot_service
        self.cache_seconds = max(1, int(cache_seconds))
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self.monotonic_provider = monotonic_provider or monotonic
        self._lock = Lock()
        self._cached_at = 0.0
        self._cached: dict[str, Any] | None = None

    def overview(self) -> dict[str, Any]:
        """Return one short-lived snapshot without starting AI, reports or orders."""
        with self._lock:
            now_tick = self.monotonic_provider()
            if self._cached is not None and now_tick - self._cached_at < self.cache_seconds:
                return self._copy(self._cached)
            payload = self._build()
            self._cached = payload
            self._cached_at = now_tick
            return self._copy(payload)

    def invalidate(self) -> None:
        """Drop the read cache after a confirmed local paper-ledger mutation."""
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    def _build(self) -> dict[str, Any]:
        """Read each authority once and publish a self-describing dashboard view."""
        now = self.now_provider()
        workbench = self.workbench.get()
        daily = self.daily.dashboard()
        operating = self.investment_os.dashboard()
        personal = self.personal_os.dashboard()
        valuation = dict(workbench.get("asset_valuation") or {})
        portfolio = dict(operating.get("portfolio") or {})
        risk = dict(operating.get("risk") or {})
        market = self._market(operating, daily, now)
        research = self._research_identity(daily)
        ai_summary = self._ai_summary(research)
        watchlist = self._watchlist(daily, ai_summary, research)
        # Actual holdings always come from Workbench/MockBroker. Portfolio Risk
        # contributes only annotations and can never replace the position ledger.
        holdings = self._holding_health(
            {"positions": list(workbench.get("positions") or [])}, risk
        )
        tasks = self._tasks(operating, personal, now)
        versions = {
            "valuation_version": valuation.get("service_version"),
            "risk_version": (risk.get("assessment") or {}).get("engine_version"),
            "ai_report_id": ai_summary.get("report_id"),
        }
        snapshot_seed = {
            "created_at": now.isoformat(),
            "valuation_version": versions["valuation_version"],
            "valued_at": valuation.get("valued_at"),
            "risk_version": versions["risk_version"],
            "ai_report_id": versions["ai_report_id"],
            "run_id": research.get("run_id"),
        }
        return {
            "service_version": INVESTMENT_DASHBOARD_VERSION,
            "snapshot_id": self._stable_id("dashboard-snapshot", snapshot_seed),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.cache_seconds)).isoformat(),
            "cache_seconds": self.cache_seconds,
            **versions,
            "market": market,
            "asset": valuation,
            "risk": {
                "availability": "available" if risk.get("assessment") or risk.get("flags") else "unavailable",
                "score": self._risk_score(risk),
                "level": self._risk_level(risk),
                "reasons": self._risk_reasons(risk),
                "assessment": risk.get("assessment"),
            },
            "ai_summary": ai_summary,
            "watchlist": watchlist,
            "holding_health": holdings,
            "next_session_plan": dict(workbench.get("next_session_plan") or {}),
            "tasks": tasks,
            "research": research,
            "data_gaps": self._data_gaps(market, valuation, risk, ai_summary, research),
            "safety": {
                "mode": "read_only_investment_dashboard",
                "asset_source": "ValuationService",
                "risk_source": "Portfolio Risk Center",
                "ai_source": "published AI Investment Copilot report only",
                "can_trade": False,
                "can_create_orders": False,
                "can_modify_strategy": False,
                "can_modify_portfolio": False,
                "can_modify_risk": False,
            },
            "can_trade": False,
            "can_create_orders": False,
        }

    @classmethod
    def _market(
        cls,
        operating: Mapping[str, Any], daily: Mapping[str, Any], now: datetime
    ) -> dict[str, Any]:
        """Preserve the upstream research-pool semantics instead of inventing a regime."""
        market = dict(operating.get("market") or {})
        formal_plan = dict(daily.get("latest_research") or {})
        preview = dict(daily.get("latest_preview") or {})
        formal = bool(formal_plan.get("run_id"))
        selected = formal_plan if formal else preview
        snapshot_at = selected.get("observed_at") or selected.get("generated_at")
        return {
            **market,
            "availability": "available" if formal or preview else "unavailable",
            "status": (
                market.get("plan_state")
                if formal
                else market.get("preview_state") or "unavailable"
            ),
            "scope": market.get("scope") or "research_pool",
            "label": market.get("label") or "研究池状态（非全市场指数）",
            "market_regime": None,
            "statement": (
                "仅展示已保存研究池状态；当前没有指数趋势、成交量或赚钱效应证据。"
            ),
            "research_date": selected.get("research_date"),
            "generated_at": selected.get("generated_at"),
            "observed_at": snapshot_at,
            "age_seconds": cls._age_seconds(snapshot_at, now),
        }

    @staticmethod
    def _research_identity(daily: Mapping[str, Any]) -> dict[str, Any]:
        """Prefer a formal close plan and label preview fallback as non-executable."""
        formal = dict(daily.get("latest_research") or {})
        preview = dict(daily.get("latest_preview") or {})
        selected = formal if formal.get("run_id") else preview
        source = "formal_close_plan" if selected is formal and selected else "intraday_preview"
        return {
            "run_id": selected.get("run_id"),
            "research_date": selected.get("research_date"),
            "generated_at": selected.get("generated_at"),
            "source_mode": source,
            "used_for_execution": bool(
                source == "formal_close_plan" and selected.get("used_for_execution")
            ),
            "candidate_count": len(list(selected.get("candidates") or [])),
        }

    def _ai_summary(self, research: Mapping[str, Any]) -> dict[str, Any]:
        """Select a published, grounded report for the active formal run without generation."""
        run_id = research.get("run_id")
        if not run_id:
            return self._unavailable_ai("当前研究没有可验证run_id，无法绑定AI建议")
        try:
            reports = list((self.copilot.reports(limit=50) or {}).get("items") or [])
        except Exception as exc:
            return self._unavailable_ai(f"AI报告读取失败：{str(exc)[:120]}")
        eligible = [
            item for item in reports
            if item.get("status") == "published"
            and item.get("run_id") == run_id
            and list(item.get("evidence_ids") or [])
        ]
        if not eligible:
            return self._unavailable_ai("当前正式研究尚无证据匹配的已发布AI报告")
        report = next(
            (item for item in eligible if item.get("subject_symbol") is None),
            eligible[0],
        )
        content = dict(report.get("content") or {})
        conclusion = content.get("summary") or content.get("conclusion") or content.get("overview")
        if not conclusion:
            conclusion = "已有证据型AI报告，请进入天机助手查看完整结论。"
        return {
            "availability": "available",
            "summary": str(conclusion),
            "run_id": str(report.get("run_id")),
            "evidence_id": str((report.get("evidence_ids") or [""])[0]),
            "evidence_ids": list(report.get("evidence_ids") or []),
            "evidence_hash": report.get("evidence_hash"),
            "report_id": report.get("report_id"),
            "report_type": report.get("report_type"),
            "subject_symbol": report.get("subject_symbol"),
            "created_at": report.get("created_time"),
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _unavailable_ai(message: str) -> dict[str, Any]:
        """Return an honest zero-authority AI placeholder."""
        return {
            "availability": "unavailable",
            "summary": message,
            "run_id": None,
            "evidence_id": None,
            "evidence_ids": [],
            "evidence_hash": None,
            "report_id": None,
            "report_type": None,
            "subject_symbol": None,
            "created_at": None,
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _watchlist(
        daily: Mapping[str, Any],
        ai_summary: Mapping[str, Any],
        research: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Expose every existing scored row without rescoring or truncation."""
        formal = dict(daily.get("latest_research") or {})
        preview = dict(daily.get("latest_preview") or {})
        selected = formal if formal.get("run_id") else preview
        items: list[dict[str, Any]] = []
        for row in list(selected.get("candidates") or []):
            risk_tags = list(row.get("risk_tags") or row.get("risk_flags") or [])
            reason = row.get("reason") or "综合评分来自统一Research Pipeline。"
            items.append({
                **dict(row),
                "score": row.get("score"),
                "investment_logic": reason,
                "risk": risk_tags[0] if risk_tags else "暂无额外风险标签",
                "risk_tags": risk_tags,
                "ai_view": (
                    ai_summary.get("summary")
                    if ai_summary.get("availability") == "available"
                    and (
                        ai_summary.get("subject_symbol") is None
                        or row.get("symbol") == ai_summary.get("subject_symbol")
                    )
                    else "暂无与该股票直接绑定的AI观点"
                ),
                "source_mode": research.get("source_mode"),
                "used_for_execution": bool(research.get("used_for_execution")),
            })
        return items

    @staticmethod
    def _holding_health(portfolio: Mapping[str, Any], risk: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Join backend-valued holdings with existing risk and exit evidence only."""
        assessment = dict(risk.get("assessment") or {})
        security_risks = {
            str(item.get("symbol")): item
            for item in list(assessment.get("security_risks") or [])
        }
        exit_signals = {
            str(item.get("symbol")): item
            for item in list((risk.get("exit_plan") or {}).get("signals") or [])
        }
        result: list[dict[str, Any]] = []
        for position in list(portfolio.get("positions") or []):
            symbol = str(position.get("symbol") or "")
            security = dict(security_risks.get(symbol) or {})
            exit_signal = dict(exit_signals.get(symbol) or {})
            level = security.get("risk_level") or "unavailable"
            result.append({
                "symbol": symbol,
                "name": position.get("name") or symbol,
                "quantity": position.get("quantity"),
                "market_value": position.get("market_value"),
                "weight": position.get("weight"),
                "pnl": position.get("unrealized_pnl"),
                "pnl_pct": position.get("unrealized_pnl_pct"),
                "health_score": None if security.get("risk_score") is None else round(100 - float(security["risk_score"]), 2),
                "risk_score": security.get("risk_score"),
                "risk_level": level,
                "score_change": None,
                "risk_change": None,
                "ai_advice": "查看退出建议" if exit_signal else "暂无已保存退出信号",
                "exit_signal": exit_signal or None,
                "data_gaps": ["缺少历史持仓评分变化"] + ([] if security else ["缺少单股风险证据"]),
            })
        return result

    @staticmethod
    def _tasks(
        operating: Mapping[str, Any], personal: Mapping[str, Any], now: datetime
    ) -> list[dict[str, Any]]:
        """Map registered operations and personal evidence into non-executing reminders."""
        recent = list(operating.get("recent_tasks") or [])
        latest_by_job: dict[str, Mapping[str, Any]] = {}
        for task in recent:
            latest_by_job.setdefault(str(task.get("job_name") or ""), task)
        jobs = {str(item.get("job_name")): item for item in list(operating.get("jobs") or [])}
        journal_count = int((personal.get("counts") or {}).get("journal_count") or 0)
        review_center = dict(personal.get("review_center") or {})
        review_counts = dict(review_center.get("counts") or {})
        important = review_center.get("most_important")

        def row(key: str, label: str, kind: str, description: str) -> dict[str, Any]:
            latest = dict(latest_by_job.get(key) or {})
            definition = dict(jobs.get(key) or {})
            return {
                "task_id": latest.get("task_id") or f"dashboard-{key}-{now.date().isoformat()}",
                "task_type": kind,
                "label": label,
                "description": description,
                "status": latest.get("status") or "pending",
                "scheduled_for": latest.get("scheduled_for") or definition.get("time_of_day"),
                "source": "Daily Investment OS" if key in jobs else "Personal OS",
                "report_id": latest.get("report_id"),
                "can_trade": False,
                "can_create_orders": False,
            }

        return [
            {
                **row("pending_reviews","待复盘","review",f"{review_counts.get('pending_reviews',0)}条日志已到复盘日期"),
                "count":int(review_counts.get("pending_reviews") or 0),
                "status":"due" if review_counts.get("pending_reviews") else "clear",
                "important":important,
            },
            {
                **row("new_reminders","新提醒","reminder",f"{review_counts.get('new_reminders',0)}条站内提醒待查看"),
                "count":int(review_counts.get("new_reminders") or 0),
                "status":"available" if review_counts.get("new_reminders") else "clear",
                "important":important,
            },
            {
                **row("unfinished_journals","未完成日志","journal",f"{review_counts.get('unfinished_journals',0)}条理由尚未完成复盘"),
                "count":int(review_counts.get("unfinished_journals") or 0),
                "status":"available" if review_counts.get("unfinished_journals") else "clear",
                "important":important,
            },
            row("closing_review", "今日复盘", "review", "查看模拟账户表现、风险和证据缺口"),
            {
                **row("investment_journal", "投资日志", "journal", "记录今天的观察、理由与教训"),
                "status": "available" if journal_count else "pending",
                "scheduled_for": None,
            },
            row("morning_report", "AI日报", "ai_report", "阅读证据型晨报；模型不可用时保留确定性摘要"),
        ]

    @staticmethod
    def _risk_score(risk: Mapping[str, Any]) -> float | None:
        assessment = dict(risk.get("assessment") or {})
        value = assessment.get("weighted_security_risk")
        return None if value is None else round(float(value), 2)

    @classmethod
    def _risk_level(cls, risk: Mapping[str, Any]) -> str:
        score = cls._risk_score(risk)
        flags = list(risk.get("flags") or [])
        if any(item.get("level") == "danger" for item in flags):
            return "high"
        if score is None:
            return "unavailable"
        return "high" if score >= 70 else "medium" if score >= 40 else "low"

    @staticmethod
    def _risk_reasons(risk: Mapping[str, Any]) -> list[str]:
        messages = [
            str(item.get("message"))
            for item in list(risk.get("flags") or [])
            if item.get("message") and item.get("level") != "success"
        ]
        if not messages:
            messages = [
                str(item.get("message"))
                for item in list((risk.get("assessment") or {}).get("risk_flags") or [])
                if item.get("message")
            ]
        return messages[:5]

    @staticmethod
    def _data_gaps(
        market: Mapping[str, Any],
        valuation: Mapping[str, Any],
        risk: Mapping[str, Any],
        ai_summary: Mapping[str, Any],
        research: Mapping[str, Any],
    ) -> list[str]:
        gaps: list[str] = []
        if not valuation.get("service_version"):
            gaps.append("ValuationService估值不可用")
        if not risk.get("assessment"):
            gaps.append("Portfolio Risk Center组合评估不可用")
        if ai_summary.get("availability") != "available":
            gaps.append(str(ai_summary.get("summary") or "证据型AI建议不可用"))
        if market.get("market_regime") is None:
            gaps.append("没有Market Regime点时证据，禁止声称牛市、震荡或熊市")
        if not research.get("run_id"):
            gaps.append("当前研究缺少run_id，AI与研究证据无法完整关联")
        return list(dict.fromkeys(gaps))

    @staticmethod
    def _stable_id(namespace: str, payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{namespace}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _age_seconds(value: Any, now: datetime) -> float | None:
        """Describe upstream market evidence age without asserting freshness."""
        if not value:
            return None
        try:
            observed = datetime.fromisoformat(str(value))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=SHANGHAI_TZ)
            current = now if now.tzinfo is not None else now.replace(tzinfo=SHANGHAI_TZ)
            return max(0.0, (current.astimezone(SHANGHAI_TZ) - observed.astimezone(SHANGHAI_TZ)).total_seconds())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _copy(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return a detached JSON-compatible value so callers cannot mutate the cache."""
        return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
