from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .coach import PersonalInvestmentCoach
from .committee import InvestmentCommittee
from .contracts import PERSONAL_OS_CONTRACT_VERSION, safety_contract
from .investment_event import InvestmentEventService
from .investor_profile import InvestorDigitalTwin
from .journal import InvestmentJournal
from .knowledge_base import PersonalKnowledgeBase
from .monthly_review import MonthlyInvestmentReview
from .personal_score import PersonalInvestmentScore
from .review_loop import InvestmentReviewLoop
from .store import PersonalOSStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PersonalInvestmentOSService:
    """Compose the user's investment loop without acquiring trading authority."""

    def __init__(
        self,
        project_root: Path,
        *,
        workbench_service: Any,
        investment_os_service: Any,
        quant_ai_service: Any,
        strategy_validation_service: Any,
        daily_research_service: Any | None = None,
        production_profile: Mapping[str, Any] | None = None,
        store: PersonalOSStore | None = None,
        now_provider: Callable[[], datetime] | None = None,
        review_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.root = Path(project_root)
        self.workbench = workbench_service
        self.investment_os = investment_os_service
        self.quant_ai = quant_ai_service
        self.strategy_validation = strategy_validation_service
        self.daily_research = daily_research_service
        self.production_profile = dict(production_profile or {})
        self.store = store or PersonalOSStore(self.root / "output" / "agent.db")
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI_TZ))
        self.profile_service = InvestorDigitalTwin(self.store)
        self.event_service = InvestmentEventService(self.store)
        self.journal_service = InvestmentJournal(self.store)
        self.knowledge_service = PersonalKnowledgeBase(self.store)
        self.score_service = PersonalInvestmentScore()
        self.coach_service = PersonalInvestmentCoach()
        self.committee_service = InvestmentCommittee()
        self.monthly_service = MonthlyInvestmentReview()
        self.review_loop = InvestmentReviewLoop(self.store, review_config)

    def dashboard(self) -> dict[str, Any]:
        """Read the whole personal loop without creating reports, events or scores."""
        now = self.now_provider()
        today = now.date().isoformat()
        workbench = self.workbench.get()
        operating = self.investment_os.dashboard()
        quant = self.quant_ai.dashboard()
        validation = self.strategy_validation.dashboard()
        events = self.store.events(80)
        journals = self.store.journals(80)
        knowledge = self.store.knowledge_items(80)
        reports = self.store.reports(50)
        reviews = self.store.reviews(80)
        reminders = self.store.reminders(100)
        counts = self.store.counts()
        profile = self.profile_service.get(self.production_profile)
        score = self.store.score()
        if score:
            score = {
                **score,
                "coverage_display": f"{float(score.get('coverage') or 0):.0%}",
                "meaning": "只评价投资流程、风险与复盘纪律，不评价盈利能力",
                "missing_weights_are_not_redistributed": True,
            }
        else:
            score = self.score_service.calculate(
                as_of_date=today,
                workbench=workbench,
                quant_ai=quant,
                counts=counts,
                reports=reports,
                journals=journals,
            )
        start = (now.date() - timedelta(days=29)).isoformat()
        coach = self.coach_service.analyze(
            period_start=start,
            period_end=today,
            profile=profile,
            events=events,
            journals=journals,
            score=score,
        )
        return {
            "service_version": PERSONAL_OS_CONTRACT_VERSION,
            "generated_at": now.isoformat(),
            "trade_date": today,
            "asset_valuation": dict(workbench.get("asset_valuation") or {}),
            "market": dict(operating.get("market") or {}),
            "portfolio": {
                "positions": list(workbench.get("positions") or []),
                "performance": dict(workbench.get("performance") or {}),
                "attribution": list(workbench.get("symbol_attribution") or []),
                "activity_summary": dict(workbench.get("activity_summary") or {}),
            },
            "risk": {
                "flags": list(workbench.get("risk_flags") or []),
                "center": dict(operating.get("risk") or {}),
            },
            "investor_profile": profile,
            "personal_score": score,
            "coach": coach,
            "investment_loop": {
                "steps": ["研究", "决策", "模拟执行", "跟踪", "复盘", "学习"],
                "current_state": self._loop_state(events, journals),
                "event_count": counts["event_count"],
                "journal_count": counts["journal_count"],
                "knowledge_count": counts["knowledge_count"],
            },
            "strategy_validation": {
                "counts": dict(validation.get("counts") or {}),
                "can_auto_promote": False,
            },
            "quant_research": {
                "evidence": dict(quant.get("evidence") or {}),
                "latest_report": quant.get("latest_report"),
            },
            "events": events[:20],
            "journals": journals[:20],
            "reviews": reviews[:20],
            "reminders": reminders[:30],
            "review_center": self._review_center(today, journals, reviews, reminders),
            "knowledge": knowledge[:20],
            "reports": self._aggregate_reports(reports, operating, quant),
            "counts": counts,
            "safety": {
                "mode": "personal_investment_learning",
                "digital_twin_is_not_production_profile": True,
                "process_score_excludes_returns": True,
                "advice_requires_evidence": True,
                **safety_contract(),
            },
            **safety_contract(),
        }

    def update_profile(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Persist only the descriptive digital twin."""
        return self.profile_service.update(values)

    def sync_events(self) -> dict[str, Any]:
        """Explicitly import existing MockBroker fills into the personal event log."""
        return self.event_service.sync_paper_trades(self.workbench.get())

    def create_event(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.event_service.create_manual(values)

    def events(self, limit: int = 200) -> dict[str, Any]:
        return {"items": self.store.events(limit), **safety_contract()}

    def create_journal(self, values: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(values)
        cycle = str(payload.pop("review_cycle", "NONE")).upper()
        custom_date = payload.pop("custom_review_date", None)
        if cycle != "NONE":
            payload["review_due_at"] = self.review_loop.due_date(
                str(payload["trade_date"]),cycle,str(custom_date) if custom_date else None,
            )
        payload.setdefault("review_status", "NOT_DUE")
        return self.journal_service.create(payload)

    def journals(self, limit: int = 200) -> dict[str, Any]:
        return {"items": self.journal_service.list(limit), **safety_contract()}

    def update_journal(self, journal_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.journal_service.update(journal_id, values)

    def archive_journal(self, journal_id: str, expected_version: int) -> dict[str, Any]:
        return self.journal_service.archive(journal_id, expected_version)

    def review_dashboard(self, *, sync: bool = False) -> dict[str, Any]:
        """Read the one-page review loop and optionally refresh deterministic reminders."""
        if sync:
            self.sync_review_reminders()
        payload = self.dashboard()
        return {
            "service_version": payload["service_version"], "generated_at": payload["generated_at"],
            "trade_date": payload["trade_date"], "asset_valuation": payload["asset_valuation"],
            "review_center": payload["review_center"], "journals": payload["journals"],
            "reviews": payload["reviews"], "reminders": payload["reminders"],
            "reports": payload["reports"], "safety": payload["safety"], **safety_contract(),
        }

    def review_draft(self, journal_id: str) -> dict[str, Any]:
        """Build a transient, non-model review draft from current backend evidence."""
        research = self.daily_research.dashboard() if self.daily_research is not None else {}
        return self.review_loop.draft(
            journal_id,workbench=self.workbench.get(),research=research,
            operating=self.investment_os.dashboard(),now=self.now_provider(),
        )

    def confirm_review(self, journal_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        """Save only an explicitly confirmed review and its optional confirmed Lesson."""
        current_draft = self.review_draft(journal_id)
        supplied = {
            (str(item.get("evidence_id") or ""),str(item.get("payload_hash") or ""))
            for item in list(values.get("evidence_refs") or []) if isinstance(item,Mapping)
        }
        expected = {
            (str(item.get("evidence_id") or ""),str(item.get("payload_hash") or ""))
            for item in list(current_draft.get("evidence_refs") or []) if isinstance(item,Mapping)
        }
        if supplied != expected:
            raise ValueError("复盘Evidence与当前权威草稿不匹配，请重新生成草稿")
        payload = {
            **dict(values),"journal_id":journal_id,"user_confirmed":True,
            "draft":current_draft,
        }
        saved, created = self.store.save_review(payload)
        lesson = None
        candidate = str(values.get("lesson_candidate") or "").strip()
        if candidate:
            journal = self.store.journal(journal_id)
            lesson = self.store.create_journal({
                "idempotency_key":f"lesson-{saved['review_id']}","entry_type":"LESSON",
                "trade_date":str(saved["reviewed_at"])[:10],"symbol":journal.get("symbol"),
                "title":f"复盘经验：{journal.get('title')}","reason":candidate,
                "user_text":candidate,"source":"USER","review_status":"DONE","evidence_ids":[],
            })
        self.store.mark_review_reminders_done(journal_id)
        return {"review":saved,"lesson":lesson,"created":created,**safety_contract()}

    def sync_review_reminders(self) -> dict[str, Any]:
        """Refresh due/risk/research reminders without running research or a model."""
        research = self.daily_research.dashboard() if self.daily_research is not None else {}
        return self.review_loop.sync_reminders(
            workbench=self.workbench.get(),research=research,
            operating=self.investment_os.dashboard(),now=self.now_provider(),
        )

    def reminders(self, limit: int = 200) -> dict[str, Any]:
        """List Personal OS in-app reminders without external delivery."""
        return {"items":self.store.reminders(limit),**safety_contract()}

    def update_reminder(self, reminder_id: str, status: str) -> dict[str, Any]:
        """Acknowledge, dismiss or finish one in-app reminder."""
        return self.store.update_reminder(reminder_id,status)

    def create_knowledge(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return self.knowledge_service.create(values)

    def knowledge(self, limit: int = 200) -> dict[str, Any]:
        return {"items": self.knowledge_service.list(limit), **safety_contract()}

    def archive_knowledge(self, knowledge_id: str, expected_version: int) -> dict[str, Any]:
        return self.knowledge_service.archive(knowledge_id, expected_version)

    def refresh_score(self, as_of_date: str | None = None) -> dict[str, Any]:
        """Persist a fixed-weight process score from current backend evidence."""
        selected = as_of_date or self.now_provider().date().isoformat()
        calculated = self.score_service.calculate(
            as_of_date=selected,
            workbench=self.workbench.get(),
            quant_ai=self.quant_ai.dashboard(),
            counts=self.store.counts(),
            reports=self.store.reports(100),
            journals=self.store.journals(200),
        )
        return {**self.store.save_score(calculated), "meaning": calculated["meaning"],
                "missing_weights_are_not_redistributed": True}

    def generate_coach(self, period_start: str | None = None, period_end: str | None = None) -> dict[str, Any]:
        end = date.fromisoformat(period_end) if period_end else self.now_provider().date()
        start = date.fromisoformat(period_start) if period_start else end - timedelta(days=29)
        score = self.store.score() or self.refresh_score(end.isoformat())
        content = self.coach_service.analyze(
            period_start=start.isoformat(), period_end=end.isoformat(),
            profile=self.profile_service.get(self.production_profile),
            events=self.store.events(500), journals=self.store.journals(500), score=score,
        )
        return self._save_report("personal_coach", start, end, content)

    def generate_weekly(self, period_start: str | None = None, period_end: str | None = None) -> dict[str, Any]:
        end = date.fromisoformat(period_end) if period_end else self.now_provider().date()
        start = date.fromisoformat(period_start) if period_start else end - timedelta(days=6)
        coach = self.coach_service.analyze(
            period_start=start.isoformat(), period_end=end.isoformat(),
            profile=self.profile_service.get(self.production_profile),
            events=self.store.events(500), journals=self.store.journals(500),
            score=self.store.score() or self.refresh_score(end.isoformat()),
        )
        content = self.committee_service.generate(
            period_start=start.isoformat(), period_end=end.isoformat(),
            workbench=self.workbench.get(), quant_ai=self.quant_ai.dashboard(),
            strategy_validation=self.strategy_validation.dashboard(), coach=coach,
        )
        return self._save_report("weekly_committee", start, end, content)

    def generate_monthly(self, period_start: str | None = None, period_end: str | None = None) -> dict[str, Any]:
        now_date = self.now_provider().date()
        end = date.fromisoformat(period_end) if period_end else now_date
        start = date.fromisoformat(period_start) if period_start else date(end.year, end.month, 1)
        score = self.store.score() or self.refresh_score(end.isoformat())
        coach = self.coach_service.analyze(
            period_start=start.isoformat(), period_end=end.isoformat(),
            profile=self.profile_service.get(self.production_profile),
            events=self.store.events(500), journals=self.store.journals(500), score=score,
        )
        content = self.monthly_service.generate(
            period_start=start.isoformat(), period_end=end.isoformat(),
            workbench=self.workbench.get(), events=self.store.events(500),
            journals=self.store.journals(500), score=score, coach=coach,
        )
        return self._save_report("monthly_review", start, end, content)

    def reports(self, limit: int = 100) -> dict[str, Any]:
        return {"items": self.store.reports(limit), **safety_contract()}

    def report(self, report_id: str) -> dict[str, Any]:
        return self.store.report(report_id)

    def _save_report(self, report_type: str, start: date, end: date, content: Mapping[str, Any]) -> dict[str, Any]:
        saved, created = self.store.save_report({
            "report_type": report_type,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "evidence": list(content["evidence"]),
            "content": {key: value for key, value in content.items() if key != "evidence"},
            "status": content["status"],
        })
        return {**saved, "created": created}

    @staticmethod
    def _loop_state(events: list[Mapping[str, Any]], journals: list[Mapping[str, Any]]) -> str:
        if any(item.get("entry_type") in {"REVIEW", "LESSON"} and item.get("status") == "active" for item in journals):
            return "复盘与学习"
        if any(item.get("event_type") in {"BUY", "SELL"} for item in events):
            return "跟踪与待复盘"
        return "研究与记录"

    @staticmethod
    def _review_center(
        today: str,journals: list[Mapping[str, Any]],reviews: list[Mapping[str, Any]],
        reminders: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Shape today's, recent and long-term review sections without new scoring."""
        active = [item for item in journals if item.get("status") == "active"]
        due = [
            item for item in active
            if item.get("review_status") in {"DUE","IN_REVIEW"}
            or (item.get("review_due_at") and str(item["review_due_at"]) <= today and item.get("review_status") != "DONE")
        ]
        cutoff = (date.fromisoformat(today)-timedelta(days=6)).isoformat()
        recent_decisions = [
            item for item in active
            if item.get("entry_type") == "DECISION" and str(item.get("trade_date") or "") >= cutoff
        ]
        recent_lessons = [item for item in active if item.get("entry_type") == "LESSON"]
        return {
            "today":{
                "due_reviews":due,
                "entries":[item for item in active if item.get("trade_date") == today],
                "reminders":[item for item in reminders if item.get("trade_date") == today],
            },
            "recent":{"decisions":recent_decisions[:20],"reviews":reviews[:20],"lessons":recent_lessons[:20]},
            "counts":{
                "pending_reviews":len(due),
                "new_reminders":sum(item.get("status") == "OPEN" for item in reminders),
                "unfinished_journals":sum(item.get("review_status") != "DONE" for item in active),
            },
            "most_important":due[0] if due else next((item for item in reminders if item.get("status") == "OPEN"),None),
            "long_term_collapsed":["weekly_reports","monthly_reports","journal_history","coach_summary"],
        }

    @staticmethod
    def _aggregate_reports(
        personal: list[Mapping[str, Any]],
        operating: Mapping[str, Any],
        quant: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        items = [
            {"source": "personal_os", **dict(item)} for item in personal
        ]
        for item in list(operating.get("recent_reports") or [])[:10]:
            items.append({"source": "daily_investment_os", **dict(item)})
        for item in list(quant.get("reports") or [])[:10]:
            items.append({"source": "ai_quant_research", **dict(item)})
        items.sort(key=lambda item: str(item.get("created_time") or item.get("created_at") or ""), reverse=True)
        return items[:30]
