from __future__ import annotations

from typing import Any

from ..core.contracts import CopilotAgentType, CopilotEvidenceBundle, CopilotReportType
from ..services.model_service import ModelService
from .agents import CoachAgent, PortfolioAgent, ResearchAgent, ReviewAgent, RiskAgent
from .evaluation import CopilotEvaluation
from .evidence_reader import EVIDENCE_READER_VERSION, EvidenceReader
from .memory_store import AI_COPILOT_STORE_VERSION, CopilotStore, CopilotStoreError
from .prompt_registry import PROMPT_REGISTRY_VERSION, PromptRegistry


COPILOT_SERVICE_VERSION = "ai-investment-copilot-v1.1.0"


class CopilotGroundingError(RuntimeError):
    """Prevent publication when model claims do not match the cited evidence."""

    def __init__(self, report_id: str, message: str) -> None:
        super().__init__(message)
        self.report_id = report_id


class CopilotService:
    """Orchestrate evidence, agents, model calls, audits and non-trading memory."""

    _REPORT_AGENTS = {
        CopilotReportType.MORNING_REPORT: CopilotAgentType.RESEARCH,
        CopilotReportType.CLOSE_REVIEW: CopilotAgentType.REVIEW,
        CopilotReportType.STOCK_ANALYSIS: CopilotAgentType.RESEARCH,
        CopilotReportType.PORTFOLIO_ANALYSIS: CopilotAgentType.PORTFOLIO,
        CopilotReportType.RISK_ALERT: CopilotAgentType.RISK,
        CopilotReportType.COACH_REVIEW: CopilotAgentType.COACH,
    }

    def __init__(self, project_root, model_service: ModelService) -> None:
        self.project_root = project_root.resolve()
        self.model_service = model_service
        self.evidence_reader = EvidenceReader(self.project_root)
        self.prompt_registry = PromptRegistry()
        self.evaluator = CopilotEvaluation()
        self.store = CopilotStore(self.project_root / "output" / "ai_copilot.db")
        self.agents = {
            CopilotAgentType.RESEARCH: ResearchAgent(),
            CopilotAgentType.PORTFOLIO: PortfolioAgent(),
            CopilotAgentType.RISK: RiskAgent(),
            CopilotAgentType.REVIEW: ReviewAgent(),
            CopilotAgentType.COACH: CoachAgent(),
        }

    def status(self) -> dict[str, Any]:
        """Return independent model, evidence and audit readiness states."""
        latest_run_id = self.evidence_reader.latest_run_id()
        model = self.model_service.status()
        return {
            "service_version": COPILOT_SERVICE_VERSION,
            "evidence_reader_version": EVIDENCE_READER_VERSION,
            "prompt_registry_version": PROMPT_REGISTRY_VERSION,
            "store_version": AI_COPILOT_STORE_VERSION,
            "latest_run_id": latest_run_id,
            "evidence_ready": bool(latest_run_id),
            "model": model,
            "agents": [agent.value for agent in CopilotAgentType],
            "report_types": [report.value for report in CopilotReportType],
            **self.store.counts(),
            "can_trade": False,
            "used_for_execution": False,
            "can_modify_score": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_create_orders": False,
        }

    def evidence(
        self,
        *,
        run_id: str | None,
        report_type: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Expose the exact bounded evidence a report would receive, without AI I/O."""
        report = CopilotReportType(report_type)
        bundle = self._read_evidence(report, run_id=run_id, symbol=symbol)
        return self.evidence_reader.public_payload(bundle)

    def generate(
        self,
        *,
        report_type: str,
        run_id: str | None = None,
        symbol: str | None = None,
        trigger: str = "user_action",
    ) -> dict[str, Any]:
        """Generate one cited report and publish it only after grounding checks pass."""
        report = CopilotReportType(report_type)
        agent_type = self._REPORT_AGENTS[report]
        normalized_symbol = symbol.strip().upper() if symbol else None
        bundle = self._read_evidence(
            report,
            run_id=run_id,
            symbol=normalized_symbol,
        )
        spec = self.prompt_registry.get(agent_type)
        model_status = self.model_service.status()
        model_version = str(model_status.get("model") or "disabled")
        task = self.store.start_task(
            task_type=report.value,
            run_id=bundle.run_id,
            subject_symbol=normalized_symbol,
            trigger=trigger,
        )
        try:
            existing = self.store.find_report(
                run_id=bundle.run_id,
                report_type=report.value,
                subject_symbol=normalized_symbol,
                model_version=model_version,
                prompt_version=spec.prompt_version,
                evidence_hash=bundle.evidence_hash,
            )
            if existing:
                self.store.complete_task(
                    task["task_id"],
                    report_id=existing["report_id"],
                    result_state="reused",
                )
                return self.store.report(existing["report_id"]) or existing

            self.store.register_prompt(spec, model_version)
            payload = self.agents[agent_type].build_payload(
                bundle,
                report,
                symbol=normalized_symbol,
            )
            completion = self.model_service.complete_json(
                spec.system_prompt,
                payload,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
            )
            normalized, evaluation = self.evaluator.evaluate(
                completion["content"],
                bundle,
                allow_memory=agent_type is CopilotAgentType.COACH,
            )
            evidence_ids = [item.evidence_id for item in bundle.evidence_items]
            saved = self.store.save_report(
                run_id=bundle.run_id,
                agent_type=agent_type.value,
                report_type=report.value,
                subject_symbol=normalized_symbol,
                model_version=str(completion["model"]),
                prompt_version=spec.prompt_version,
                evidence_ids=evidence_ids,
                evidence_hash=bundle.evidence_hash,
                content=normalized,
                status="published" if evaluation["grounded"] else "rejected",
                error_message=None if evaluation["grounded"] else "AI引用或数字未通过证据校验",
            )
            self.store.save_evaluation(saved["report_id"], evaluation)
            saved = self.store.report(saved["report_id"]) or saved
            if not evaluation["grounded"]:
                self.store.fail_task(
                    task["task_id"],
                    error_message="AI引用或数字未通过证据校验",
                    report_id=saved["report_id"],
                    result_state="rejected",
                )
                raise CopilotGroundingError(
                    saved["report_id"],
                    "AI输出未通过证据引用或数字校验，已拒绝发布",
                )
            self._sync_profile_memory(bundle)
            if agent_type is CopilotAgentType.COACH:
                for memory in normalized["memory_candidates"]:
                    self.store.add_memory(
                        category=memory["category"],
                        content=memory["content"],
                        source="ai_inference",
                        confidence=float(memory["confidence"]),
                        evidence_ids=list(memory["evidence_ids"]),
                        source_report_id=saved["report_id"],
                        confirmed=False,
                    )
            self.store.complete_task(
                task["task_id"],
                report_id=saved["report_id"],
                result_state="generated",
            )
            return self.store.report(saved["report_id"]) or saved
        except CopilotGroundingError:
            raise
        except Exception as exc:
            try:
                self.store.fail_task(
                    task["task_id"],
                    error_message=f"{type(exc).__name__}: AI任务执行失败",
                    result_state="error",
                )
            except CopilotStoreError:
                # Preserve the original failure if task finalization itself failed.
                pass
            raise

    def report(self, report_id: str) -> dict[str, Any]:
        """Read one persisted report and its evaluation."""
        report = self.store.report(report_id)
        if not report:
            raise CopilotStoreError("AI报告不存在")
        return report

    def reports(self, limit: int = 30) -> dict[str, Any]:
        """List recent reports without triggering model generation."""
        return {
            "items": self.store.list_reports(limit),
            "can_trade": False,
            "used_for_execution": False,
        }

    def add_user_memory(
        self,
        *,
        category: str,
        content: str,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Save one explicit user memory as confirmed, non-executable context."""
        return self.store.add_memory(
            category=category,
            content=content,
            source="user_confirmed",
            confidence=confidence,
            evidence_ids=[],
            confirmed=True,
        )

    def memories(self, limit: int = 100) -> dict[str, Any]:
        """List investment memory without feeding it into deterministic engines."""
        return {
            "items": self.store.list_memory(limit),
            "can_affect_execution": False,
        }

    def confirm_memory(self, memory_id: str) -> dict[str, Any]:
        """Confirm one AI-derived memory candidate through explicit user action."""
        return self.store.confirm_memory(memory_id)

    def rate_report(self, report_id: str, rating: int, note: str = "") -> dict[str, Any]:
        """Attach a human quality rating to the immutable AI report."""
        return self.store.rate_report(report_id, rating, note)

    def _read_evidence(
        self,
        report: CopilotReportType,
        *,
        run_id: str | None,
        symbol: str | None,
    ) -> CopilotEvidenceBundle:
        """Route report types through the bounded Evidence Reader public contracts."""
        if report is CopilotReportType.STOCK_ANALYSIS:
            return self.evidence_reader.get_stock_evidence(symbol or "", run_id)
        if report is CopilotReportType.PORTFOLIO_ANALYSIS:
            return self.evidence_reader.get_portfolio_evidence(run_id)
        if report is CopilotReportType.RISK_ALERT:
            return self.evidence_reader.get_risk_evidence(run_id)
        if report is CopilotReportType.CLOSE_REVIEW:
            return self.evidence_reader.get_review_history(run_id)
        return self.evidence_reader.read(run_id, scope=report.value, symbol=symbol)

    def _sync_profile_memory(self, bundle) -> None:
        """Store the versioned Investment Profile as confirmed source-owned memory."""
        profile_item = next(
            (
                item
                for item in bundle.evidence_items
                if item.evidence_id == "E-PR-PROFILE"
            ),
            None,
        )
        if profile_item is None:
            return
        profile = profile_item.payload
        content = (
            f"risk_level={profile.get('risk_level')};"
            f"investment_horizon={profile.get('investment_horizon')};"
            f"max_drawdown_tolerance={profile.get('max_drawdown_tolerance')};"
            f"investment_style={profile.get('investment_style')}"
        )
        self.store.add_memory(
            category="profile",
            content=content,
            source="investment_profile",
            confidence=1.0,
            evidence_ids=[profile_item.evidence_id],
            confirmed=True,
        )
