from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PERSONAL_AI_ASSISTANT_VERSION = "personal-ai-assistant-v1.0.0"

ASSISTANT_INTENTS = {
    "daily_attention",
    "portfolio_analysis",
    "stock_reason",
    "portfolio_fit",
    "behavior_review",
}

SUGGESTED_NEXT_ACTIONS = {
    "继续观察",
    "查看风险详情",
    "完成复盘",
    "等待正式收盘run",
    "记录投资理由",
}

# This is the complete business-read capability ceiling of the personal assistant.
# The names are product contracts rather than Python reflection targets: adapters may
# satisfy them by reshaping the existing Dashboard, Evidence Reader and Personal OS.
READ_ONLY_EVIDENCE_WHITELIST = (
    "get_dashboard_overview",
    "get_research_evidence",
    "get_stock_factor_evidence",
    "get_portfolio_snapshot",
    "get_portfolio_risk",
    "get_exit_evidence",
    "get_investment_profile",
    "get_investment_journal",
    "get_review_history",
)

_DIRECTIVE_PATTERN = re.compile(
    r"(?:建议|应当|应该|需要|立即|马上|可以|宜)\s*(?:执行|进行|考虑)?\s*"
    r"(?:立即|马上)?\s*(?:买入|卖出|加仓|减仓|清仓|下单)",
    re.IGNORECASE,
)


class PersonalAIAssistantService:
    """Answer five evidence-bound questions without gaining workflow authority."""

    _REPORT_BY_INTENT = {
        "daily_attention": "morning_report",
        "portfolio_analysis": "portfolio_analysis",
        "stock_reason": "stock_analysis",
        "portfolio_fit": "stock_analysis",
    }

    _ACTION_BY_INTENT = {
        "daily_attention": "继续观察",
        "portfolio_analysis": "查看风险详情",
        "stock_reason": "记录投资理由",
        "portfolio_fit": "继续观察",
        "behavior_review": "完成复盘",
    }

    def __init__(
        self,
        *,
        investment_dashboard_service: Any,
        copilot_service: Any,
        personal_os_service: Any,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.dashboard = investment_dashboard_service
        self.copilot = copilot_service
        self.personal_os = personal_os_service
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))

    def query(
        self,
        *,
        intent: str,
        symbol: str | None = None,
        question: str | None = None,
    ) -> dict[str, Any]:
        """Run one explicit, user-triggered query; never start research or execution."""
        selected = str(intent).strip()
        if selected not in ASSISTANT_INTENTS:
            raise ValueError("不支持的AI助手问题类型")
        normalized_symbol = str(symbol or "").strip().upper() or None
        if selected in {"stock_reason", "portfolio_fit"} and not normalized_symbol:
            raise ValueError("该问题必须提供股票代码")

        if selected == "behavior_review":
            return self._behavior_review(selected, normalized_symbol, question)

        snapshot = self.dashboard.overview()
        research = dict(snapshot.get("research") or {})
        run_id = research.get("run_id")
        if research.get("source_mode") != "formal_close_plan" or not run_id:
            return self._unavailable(
                selected,
                normalized_symbol,
                "当前只有盘中预览或尚无正式收盘研究，AI不生成正式投资结论。",
                list(snapshot.get("data_gaps") or [])
                + ["等待带run_id的正式收盘研究证据"],
                action="等待正式收盘run",
            )

        report_type = self._REPORT_BY_INTENT[selected]
        try:
            evidence = self.copilot.evidence(
                run_id=str(run_id),
                report_type=report_type,
                symbol=normalized_symbol,
            )
            report = self.copilot.generate(
                report_type=report_type,
                run_id=str(run_id),
                symbol=normalized_symbol,
                trigger="user_action",
            )
            return self._from_report(
                intent=selected,
                symbol=normalized_symbol,
                report=report,
                evidence=evidence,
                snapshot=snapshot,
            )
        except Exception as exc:  # Fail closed: a model/evidence outage cannot become advice.
            return self._unavailable(
                selected,
                normalized_symbol,
                "AI证据或模型当前不可用，未生成投资结论。",
                list(snapshot.get("data_gaps") or [])
                + [f"AI助手降级：{type(exc).__name__}"],
                action=self._ACTION_BY_INTENT[selected],
            )

    def _from_report(
        self,
        *,
        intent: str,
        symbol: str | None,
        report: Mapping[str, Any],
        evidence: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        content = dict(report.get("content") or {})
        filtered = False

        def safe(value: Any) -> str:
            nonlocal filtered
            text = str(value or "").strip()
            if _DIRECTIVE_PATTERN.search(text):
                filtered = True
                return "该段包含交易导向措辞，已按只读安全边界隐藏。"
            return text

        key_points = []
        for item in list(content.get("findings") or [])[:8]:
            if not isinstance(item, Mapping):
                continue
            title = safe(item.get("title") or "关键依据")
            detail = safe(item.get("detail"))
            key_points.append(f"{title}：{detail}" if detail else title)
        risks = [
            safe(item.get("message"))
            for item in list(content.get("risks") or [])[:6]
            if isinstance(item, Mapping) and str(item.get("message") or "").strip()
        ]
        summary = safe(content.get("summary")) or "已生成证据型解释。"
        uncertainties = list(dict.fromkeys(
            [str(item) for item in list(evidence.get("data_gaps") or [])]
            + [str(item) for item in list(snapshot.get("data_gaps") or [])]
            + (["模型文本触发交易指令过滤，相关段落未展示"] if filtered else [])
        ))
        if intent == "portfolio_fit":
            risk = dict(snapshot.get("risk") or {})
            key_points.append(
                f"当前组合风险：{risk.get('level') or '不可用'}；"
                "本回答未把个股研究评分转换为个人适配评分。"
            )
            risks.extend(str(item) for item in list(risk.get("reasons") or [])[:3])
            try:
                personal = self.personal_os.dashboard()
                profile = dict(personal.get("investor_profile") or {})
                if profile:
                    key_points.append(
                        "投资画像：风险等级"
                        f"{profile.get('risk_level') or '不可用'}，最大回撤容忍"
                        f"{profile.get('max_drawdown_tolerance') or profile.get('max_drawdown') or '不可用'}。"
                    )
                else:
                    uncertainties.append("Investment Profile不可用")
            except Exception:
                uncertainties.append("Investment Profile读取失败")
            uncertainties.append("当前未建立个股与个人画像的点时适配评分，只展示个股证据与组合风险上下文")
        return {
            "service_version": PERSONAL_AI_ASSISTANT_VERSION,
            "intent": intent,
            "status": "degraded" if uncertainties else "published",
            "generated_at": self.now_provider().isoformat(),
            "symbol": symbol,
            "question": None,
            "summary": summary,
            "key_points": key_points,
            "risks": risks,
            "uncertainties": list(dict.fromkeys(uncertainties)),
            "evidence_refs": self._evidence_refs(report, evidence),
            "journal_context": self._journal_context(symbol),
            "suggested_next_action": self._ACTION_BY_INTENT[intent],
            "model_used": True,
            "read_only_whitelist": list(READ_ONLY_EVIDENCE_WHITELIST),
            "safety": self._safety(),
            **self._capabilities(),
        }

    def _behavior_review(
        self,
        intent: str,
        symbol: str | None,
        question: str | None,
    ) -> dict[str, Any]:
        try:
            personal = self.personal_os.dashboard()
            coach = dict(personal.get("coach") or {})
        except Exception as exc:
            return self._unavailable(
                intent,
                symbol,
                "个人投资日志或复盘证据当前不可用。",
                [f"行为复盘降级：{type(exc).__name__}"],
                action="完成复盘",
            )
        confirmed_reviews = list(personal.get("reviews") or [])
        confirmed_lessons = [
            item for item in list(personal.get("journals") or [])
            if item.get("entry_type") == "LESSON" and item.get("status") == "active"
        ]
        sample_count = len(confirmed_reviews)+len(confirmed_lessons)
        key_points = [
            str(item.get("statement") or item.get("pattern") or "")
            for item in list(coach.get("patterns") or [])[:8]
            if isinstance(item, Mapping)
        ]
        key_points.extend(str(item) for item in list(coach.get("improvements") or [])[:5])
        refs = []
        for item in list(coach.get("evidence") or []):
            if not isinstance(item, Mapping) or not item.get("evidence_id"):
                continue
            refs.append({
                "evidence_id": str(item["evidence_id"]),
                "source": str(item.get("source_type") or "personal_os"),
                "observed_at": item.get("observed_at"),
                "data_time": item.get("observed_at"),
                "run_id": None,
                "report_id": None,
                "strategy_version": None,
                "factor_version": None,
                "evidence_hash": item.get("payload_hash"),
            })
        gaps = [str(item) for item in list(coach.get("data_gaps") or [])]
        if sample_count < 3:
            gaps.append("当前样本不足，无法形成稳定结论")
        return {
            "service_version": PERSONAL_AI_ASSISTANT_VERSION,
            "intent": intent,
            "status": str(coach.get("status") or "degraded"),
            "generated_at": self.now_provider().isoformat(),
            "symbol": symbol,
            "question": question,
            "summary": (
                f"基于最近{sample_count}条已确认记录进行复核。"
                if sample_count else "当前样本不足，无法形成稳定结论。"
            ),
            "key_points": list(dict.fromkeys(filter(None, key_points))),
            "risks": [],
            "uncertainties": gaps,
            "evidence_refs": refs,
            "suggested_next_action": "完成复盘",
            "model_used": False,
            "read_only_whitelist": list(READ_ONLY_EVIDENCE_WHITELIST),
            "safety": self._safety(),
            **self._capabilities(),
        }

    def _journal_context(self, symbol: str | None) -> dict[str, Any]:
        """Return user-authored reasons separately from AI summaries for one holding."""
        if not symbol:
            return {"availability":"not_applicable","items":[]}
        try:
            payload = self.personal_os.journals(200)
            items = [
                {
                    "journal_id":item.get("journal_id"),"entry_type":item.get("entry_type"),
                    "trade_date":item.get("trade_date"),"user_text":item.get("user_text"),
                    "ai_summary":item.get("ai_summary"),"review_status":item.get("review_status"),
                }
                for item in list(payload.get("items") or [])
                if item.get("symbol") == symbol and item.get("entry_type") in {"OBSERVE","DECISION"}
            ]
        except Exception:
            return {"availability":"unavailable","items":[]}
        return {"availability":"available" if items else "unavailable","items":items[:5]}

    @staticmethod
    def _evidence_refs(
        report: Mapping[str, Any], evidence: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        cited = {str(item) for item in list(report.get("evidence_ids") or [])}
        refs: list[dict[str, Any]] = []
        for item in list(evidence.get("evidence_items") or []):
            if not isinstance(item, Mapping):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id not in cited:
                continue
            refs.append({
                "evidence_id": evidence_id,
                "source": str(item.get("source") or "unknown"),
                "observed_at": item.get("observed_at"),
                "data_time": item.get("observed_at"),
                "run_id": report.get("run_id") or evidence.get("run_id"),
                "report_id": report.get("report_id"),
                "strategy_version": evidence.get("strategy_version"),
                "factor_version": evidence.get("factor_version"),
                "evidence_hash": report.get("evidence_hash") or evidence.get("evidence_hash"),
            })
        return refs

    def _unavailable(
        self,
        intent: str,
        symbol: str | None,
        summary: str,
        gaps: list[str],
        *,
        action: str,
    ) -> dict[str, Any]:
        if action not in SUGGESTED_NEXT_ACTIONS:
            raise ValueError("AI助手后续动作不在安全白名单")
        return {
            "service_version": PERSONAL_AI_ASSISTANT_VERSION,
            "intent": intent,
            "status": "unavailable",
            "generated_at": self.now_provider().isoformat(),
            "symbol": symbol,
            "question": None,
            "summary": summary,
            "key_points": [],
            "risks": [],
            "uncertainties": list(dict.fromkeys(str(item) for item in gaps if item)),
            "evidence_refs": [],
            "suggested_next_action": action,
            "model_used": False,
            "read_only_whitelist": list(READ_ONLY_EVIDENCE_WHITELIST),
            "safety": self._safety(),
            **self._capabilities(),
        }

    @staticmethod
    def _safety() -> dict[str, Any]:
        return {
            "mode": "evidence_bound_personal_assistant",
            "explicit_user_trigger_required": True,
            "market_regime_inference_enabled": False,
            "order_language_allowed": False,
            **PersonalAIAssistantService._capabilities(),
        }

    @staticmethod
    def _capabilities() -> dict[str, bool]:
        return {
            "used_for_execution": False,
            "can_affect_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_launch_experiment": False,
            "can_auto_remediate": False,
        }
