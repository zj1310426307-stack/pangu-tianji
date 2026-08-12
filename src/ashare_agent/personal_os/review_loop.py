from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping

from .contracts import content_hash, safety_contract, stable_id
from .store import PersonalOSStore


class InvestmentReviewLoop:
    """Build factual review drafts and deterministic in-app reminders.

    This coordinator never calls a model, calculates a new factor/risk score, or
    sends an order. It only reshapes snapshots already owned by Workbench,
    ResearchPipeline and Portfolio Risk/Exit services.
    """

    def __init__(self, store: PersonalOSStore, config: Mapping[str, Any] | None = None) -> None:
        self.store = store
        self.config = dict(config or {})
        self.score_change_threshold = float(self.config.get("score_change_threshold", 15.0))
        self.candidate_hold_rank = int(self.config.get("candidate_hold_rank", 20))

    @staticmethod
    def due_date(trade_date: str, review_cycle: str, custom_date: str | None = None) -> str:
        """Resolve the bounded T+5, T+20 or user-selected calendar date."""
        base = date.fromisoformat(trade_date)
        selected = str(review_cycle).upper()
        if selected == "T5":
            return (base + timedelta(days=5)).isoformat()
        if selected == "T20":
            return (base + timedelta(days=20)).isoformat()
        if selected == "CUSTOM" and custom_date:
            target = date.fromisoformat(custom_date)
            if target < base:
                raise ValueError("自定义复盘日期不能早于日志日期")
            return target.isoformat()
        raise ValueError("复盘周期只支持T5、T20或CUSTOM")

    def draft(
        self,
        journal_id: str,
        *,
        workbench: Mapping[str, Any],
        research: Mapping[str, Any],
        operating: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """Assemble a transient fact draft without saving review or Lesson state."""
        journal = self.store.journal(journal_id)
        symbol = str(journal.get("symbol") or "")
        formal = dict(research.get("latest_research") or {})
        candidates = {
            str(item.get("symbol") or ""): dict(item)
            for item in list(formal.get("candidates") or [])
            if isinstance(item, Mapping)
        }
        candidate = candidates.get(symbol)
        positions = {
            str(item.get("symbol") or ""): dict(item)
            for item in list(workbench.get("positions") or [])
            if isinstance(item, Mapping)
        }
        position = positions.get(symbol)
        risk_center = dict(operating.get("risk") or {})
        assessment = dict(risk_center.get("assessment") or {})
        risks = {
            str(item.get("symbol") or ""): dict(item)
            for item in list(assessment.get("security_risks") or [])
            if isinstance(item, Mapping)
        }
        exits = {
            str(item.get("symbol") or ""): dict(item)
            for item in list((risk_center.get("exit_plan") or {}).get("signals") or [])
            if isinstance(item, Mapping)
        }
        evidence_refs: list[dict[str, Any]] = []
        if formal.get("run_id"):
            evidence_refs.append({
                "evidence_id": f"E-REVIEW-RESEARCH-{journal_id}",
                "source_type": "ResearchPipeline",
                "source_id": str(formal["run_id"]),
                "observed_at": formal.get("observed_at") or formal.get("generated_at"),
                "payload_hash": content_hash(candidate or {"symbol": symbol, "state": "not_ranked"}),
            })
        valuation = dict(workbench.get("asset_valuation") or {})
        if valuation.get("valued_at"):
            evidence_refs.append({
                "evidence_id": f"E-REVIEW-VALUATION-{journal_id}",
                "source_type": "ValuationService",
                "source_id": str(valuation.get("service_version") or "valuation"),
                "observed_at": valuation.get("valued_at"),
                "payload_hash": content_hash({"position": position, "valuation": valuation}),
            })
        if risks.get(symbol) or exits.get(symbol):
            evidence_refs.append({
                "evidence_id": f"E-REVIEW-RISK-{journal_id}",
                "source_type": "Portfolio Risk / Exit",
                "source_id": str(assessment.get("portfolio_id") or formal.get("run_id") or "current"),
                "observed_at": assessment.get("as_of") or now.isoformat(),
                "payload_hash": content_hash({"risk": risks.get(symbol), "exit": exits.get(symbol)}),
            })
        gaps = []
        if not formal.get("run_id"):
            gaps.append("缺少正式收盘Research run，当前评分不可核验")
        if symbol and candidate is None:
            gaps.append("该股票当前不在正式候选区间")
        if symbol and position is None:
            gaps.append("当前模拟账户没有该股票持仓")
        if symbol and not risks.get(symbol):
            gaps.append("缺少该股票当前权威风险快照")
        return {
            "draft_id": stable_id("review-draft",journal_id,now.isoformat()),
            "journal_id": journal_id,
            "generated_at": now.isoformat(),
            "questions": [
                "当时为什么关注、持有或做出这个判断？",
                "原投资逻辑现在还成立吗？",
                "当时最担心的风险发生了吗？",
                "结果如何？",
                "最大错误是什么？",
                "这次应该留下什么经验？",
            ],
            "original": {
                "reason": journal.get("reason"),
                "expected_condition": journal.get("expected_condition"),
                "invalid_condition": journal.get("invalid_condition"),
                "risk_notes": journal.get("risk_notes"),
                "user_text": journal.get("user_text"),
                "ai_summary": journal.get("ai_summary"),
            },
            "facts_changed": {
                "current_score": candidate.get("score") if candidate else None,
                "current_rank": candidate.get("rank") if candidate else None,
                "current_risk": risks.get(symbol),
                "current_position": position,
                "current_return": position.get("unrealized_pnl_pct") if position else None,
                "exit_intent": exits.get(symbol),
            },
            "still_supported": [] if candidate is None else ["当前正式研究仍包含该股票"],
            "invalidated": [] if not exits.get(symbol) else ["已有Exit Engine退出证据，需要用户重点复核"],
            "uncertainties": gaps,
            "review_focus": ["逐条核对原失效条件", "区分结果好坏与当时决策质量"],
            "evidence_refs": evidence_refs,
            "user_confirmation_required": True,
            "saved": False,
            **safety_contract(),
        }

    def sync_reminders(
        self,
        *,
        workbench: Mapping[str, Any],
        research: Mapping[str, Any],
        operating: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """Create due/change reminders from existing snapshots with strict deduplication."""
        trade_date = now.date().isoformat()
        created: list[dict[str, Any]] = []
        journals = [item for item in self.store.journals(500) if item.get("status") == "active"]
        for journal in journals:
            due = journal.get("review_due_at")
            if due and due <= trade_date and journal.get("review_status") != "DONE":
                self.store.mark_journal_due(str(journal["journal_id"]))
                reminder, was_created = self.store.create_reminder({
                    "reminder_type": "REVIEW_DUE", "trade_date": trade_date,
                    "symbol": journal.get("symbol") or "", "title": "投资日志待复盘",
                    "message": f"《{journal.get('title')}》已到复盘日期。",
                    "source_type": "investment_journal", "source_id": journal["journal_id"],
                    "evidence_refs": [{"journal_id": journal["journal_id"], "review_due_at": due}],
                })
                if was_created:
                    created.append(reminder)
        unfinished = [
            item for item in journals
            if item.get("trade_date") == trade_date
            and item.get("entry_type") in {"OBSERVE","DECISION"}
            and item.get("review_status") != "DONE"
        ]
        if unfinished:
            reminder, was_created = self.store.create_reminder({
                "reminder_type": "DAILY_REVIEW", "trade_date": trade_date, "symbol": "",
                "title": "今日投资记录待补充",
                "message": f"今日有{len(unfinished)}条日志需要补充或复盘。",
                "source_type": "investment_journal", "source_id": "daily-review",
                "evidence_refs": [{"journal_ids": [item["journal_id"] for item in unfinished[:20]]}],
            })
            if was_created:
                created.append(reminder)
        created.extend(self._sync_risk_changes(workbench, operating, trade_date, now))
        created.extend(self._sync_research_changes(workbench, research, trade_date, now))
        return {
            "created_count": len(created), "created": created,
            "items": self.store.reminders(200), **safety_contract(),
        }

    def _sync_risk_changes(
        self, workbench: Mapping[str, Any], operating: Mapping[str, Any],
        trade_date: str, now: datetime,
    ) -> list[dict[str, Any]]:
        """Compare authoritative per-security risks; missing history means no alert."""
        assessment = dict((operating.get("risk") or {}).get("assessment") or {})
        current = {
            str(item.get("symbol") or ""): dict(item)
            for item in list(assessment.get("security_risks") or [])
            if isinstance(item, Mapping) and item.get("symbol")
        }
        position_symbols = {
            str(item.get("symbol") or "") for item in list(workbench.get("positions") or [])
        }
        created = []
        for symbol in sorted(position_symbols & set(current)):
            previous = self.store.latest_snapshot("RISK", symbol)
            payload = current[symbol]
            source_id = str(assessment.get("portfolio_id") or assessment.get("as_of") or trade_date)
            if previous:
                before = str((previous.get("payload") or {}).get("risk_level") or "")
                after = str(payload.get("risk_level") or "")
                before_score = previous.get("payload",{}).get("risk_score")
                after_score = payload.get("risk_score")
                changed = before != after or (
                    before_score is not None and after_score is not None
                    and abs(float(after_score)-float(before_score)) >= self.score_change_threshold
                )
                if changed:
                    reminder, was_created = self.store.create_reminder({
                        "reminder_type":"RISK_CHANGED","trade_date":trade_date,"symbol":symbol,
                        "title":f"{symbol} 风险证据变化",
                        "message":f"权威风险快照由{before or '未知'}变为{after or '未知'}，请复核。",
                        "source_type":"portfolio_risk_snapshot","source_id":source_id,
                        "evidence_refs":[{"previous_snapshot_id":previous["snapshot_id"]}],
                    })
                    if was_created:
                        created.append(reminder)
            self.store.save_snapshot({
                "snapshot_type":"RISK","trade_date":trade_date,"symbol":symbol,
                "source_id":source_id,"payload_hash":content_hash(payload),"payload":payload,
                "observed_at":str(assessment.get("as_of") or now.isoformat()),
            })
        return created

    def _sync_research_changes(
        self, workbench: Mapping[str, Any], research: Mapping[str, Any],
        trade_date: str, now: datetime,
    ) -> list[dict[str, Any]]:
        """Compare formal ResearchPipeline ranks and scores for current holdings only."""
        formal = dict(research.get("latest_research") or {})
        if not formal.get("run_id"):
            return []
        ranking = {
            str(item.get("symbol") or ""): dict(item)
            for item in list(formal.get("candidates") or [])
            if isinstance(item, Mapping) and item.get("symbol")
        }
        created = []
        for position in list(workbench.get("positions") or []):
            symbol = str(position.get("symbol") or "")
            payload = ranking.get(symbol) or {"symbol":symbol,"rank":None,"score":None,"in_candidates":False}
            previous = self.store.latest_snapshot("RESEARCH",symbol)
            if previous:
                prior = dict(previous.get("payload") or {})
                old_score,new_score = prior.get("score"),payload.get("score")
                fell_out = prior.get("in_candidates",True) and not payload.get("in_candidates",True)
                score_changed = old_score is not None and new_score is not None and abs(float(new_score)-float(old_score)) >= self.score_change_threshold
                rank_changed = prior.get("rank") is not None and payload.get("rank") is not None and int(prior["rank"]) <= self.candidate_hold_rank < int(payload["rank"])
                if fell_out or score_changed or rank_changed:
                    reminder, was_created = self.store.create_reminder({
                        "reminder_type":"RESEARCH_CHANGED","trade_date":trade_date,"symbol":symbol,
                        "title":f"{symbol} 研究证据变化",
                        "message":"持仓的正式Research排名或评分发生显著变化，请复核原投资逻辑。",
                        "source_type":"ResearchPipeline","source_id":str(formal["run_id"]),
                        "evidence_refs":[{"previous_snapshot_id":previous["snapshot_id"],"run_id":formal["run_id"]}],
                    })
                    if was_created:
                        created.append(reminder)
            self.store.save_snapshot({
                "snapshot_type":"RESEARCH","trade_date":trade_date,"symbol":symbol,
                "source_id":str(formal["run_id"]),"payload_hash":content_hash(payload),
                "payload":payload,"observed_at":str(formal.get("observed_at") or formal.get("generated_at") or now.isoformat()),
            })
        return created
