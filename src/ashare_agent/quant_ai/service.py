from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..quant_lab.contracts import utc_now
from ..quant_lab.validation_gate.service import StrategyValidationService
from ..services.model_service import ModelService
from .contracts import AnalystResult, ResearchEvidenceBundle
from .evaluation import QuantAIResearchEvaluation
from .experiment_reader import QuantAIExperimentReader
from .factor_analyst import FactorAnalyst
from .market_analyst import MarketAnalyst
from .report_generator import MODEL_SYSTEM_PROMPT, ResearchReportGenerator
from .research_memory import QuantAIStoreError, ResearchMemoryStore
from .risk_analyst import RiskAnalyst
from .strategy_analyst import StrategyAnalyst


QUANT_AI_SERVICE_VERSION = "ai-quant-research-analyst-v1.0.0"


class QuantAIResearchUnavailable(RuntimeError):
    """Signal that no sealed evidence exists for a research report."""


class AIQuantResearchService:
    """Orchestrate read-only agents, model grounding, memory and research questions."""

    def __init__(
        self,
        project_root: Path,
        *,
        model_service: ModelService | None = None,
        strategy_validation_service: StrategyValidationService | None = None,
        reader: QuantAIExperimentReader | None = None,
        store: ResearchMemoryStore | None = None,
    ) -> None:
        self.root = Path(project_root)
        self.model_service = model_service or ModelService()
        self.strategy_validation = (
            strategy_validation_service or StrategyValidationService(self.root)
        )
        self.reader = reader or QuantAIExperimentReader(
            self.root,
            strategy_registry=self.strategy_validation.registry,
        )
        # Reuse the established local database so legacy-codepage Windows builds
        # do not need to create a second SQLite file through a Unicode junction.
        self.store = store or ResearchMemoryStore(self.root / "output" / "agent.db")
        self.evaluator = QuantAIResearchEvaluation()
        self.report_generator = ResearchReportGenerator()
        self.analysts = (
            StrategyAnalyst(), FactorAnalyst(), RiskAnalyst(), MarketAnalyst(),
        )

    def evidence(self, review_id: str | None = None) -> dict[str, Any]:
        """Expose the exact bounded evidence without invoking the model."""
        bundle = self.reader.read(review_id) if review_id else self.reader.latest()
        return bundle.public_payload()

    def generate(
        self,
        *,
        review_id: str | None = None,
        scheduled_for: str | None = None,
        trigger: str = "user_action",
    ) -> dict[str, Any]:
        """Generate one idempotent brief and publish model text only when grounded."""
        slot = scheduled_for or utc_now()
        task = self.store.start_task(slot, trigger)
        if task["status"] != "running":
            report_id = task.get("result_report_id")
            if report_id:
                return self.store.report(str(report_id))
            raise QuantAIStoreError("该AI研究任务槽已经终结，禁止自动重试")
        try:
            bundle = self.reader.read(review_id) if review_id else self.reader.latest()
            if not bundle.items:
                raise QuantAIResearchUnavailable("没有可引用的Strategy Validation证据")
            results = tuple(analyst.analyze(bundle) for analyst in self.analysts)
            deterministic_claims = [
                claim
                for result in results
                for claim in result.as_dict()["claims"]
            ]
            deterministic_eval = self.evaluator.evaluate_claims(
                deterministic_claims, bundle
            )
            if not deterministic_eval["grounded"]:
                raise QuantAIStoreError("确定性研究结论未通过自身证据校验")

            model_version, model_analysis, model_eval = self._model_analysis(
                bundle, results
            )
            previous = next(iter(self.store.reports(limit=1)), None)
            report = self.report_generator.generate(
                bundle=bundle,
                analyst_results=results,
                deterministic_evaluation=deterministic_eval,
                model_analysis=model_analysis,
                model_evaluation=model_eval,
                model_version=model_version,
                previous_report=previous,
            )
            saved = self.store.save_report(report)
            self._save_memory_and_questions(saved, results)
            self.store.finish_task(
                str(task["task_id"]), status="succeeded", report_id=saved["report_id"]
            )
            return saved
        except Exception as exc:
            try:
                self.store.finish_task(
                    str(task["task_id"]),
                    status="failed",
                    error_message=f"{type(exc).__name__}: AI量化研究任务失败",
                )
            except QuantAIStoreError:
                pass
            raise

    def dashboard(self) -> dict[str, Any]:
        """Return a backend-owned AI Research Center snapshot with zero authority."""
        reports = self.store.reports(limit=30)
        memories = self.store.memories(limit=100)
        questions = self.store.questions(limit=100)
        tasks = self.store.tasks(limit=30)
        try:
            evidence = self.reader.latest().public_payload()
        except Exception as exc:
            evidence = {
                "evidence_items": [],
                "data_gaps": [f"{type(exc).__name__}: evidence_read_failed"],
                "can_trade": False,
                "can_create_orders": False,
            }
        latest = reports[0] if reports else None
        return {
            "service_version": QUANT_AI_SERVICE_VERSION,
            "generated_at": utc_now(),
            "model": self.model_service.status(),
            "evidence": {
                "strategy_id": evidence.get("strategy_id"),
                "strategy_version": evidence.get("strategy_version"),
                "dataset_label": evidence.get("dataset_label"),
                "evidence_hash": evidence.get("evidence_hash"),
                "evidence_count": len(evidence.get("evidence_items") or []),
                "data_gaps": evidence.get("data_gaps") or [],
            },
            "latest_report": latest,
            "reports": reports,
            "memory": memories,
            "questions": questions,
            "tasks": tasks,
            "counts": self.store.counts(),
            "schedule": {
                "job_name": "ai_quant_morning_brief",
                "time_of_day": "08:00",
                "timezone": "Asia/Shanghai",
                "trading_days_only": True,
                "calendar_source": "weekday_fallback_unless_injected",
                "official_holiday_safe": False,
                "missed_slots_backfilled": False,
            },
            "agents": [
                {"agent_type": analyst.agent_type.value, "permission": "RESEARCH_READ_ONLY"}
                for analyst in self.analysts
            ],
            "safety": {
                "research_read_only": True,
                "model_output_requires_grounding": True,
                "memory_not_used_for_execution": True,
                "questions_do_not_launch_experiments": True,
                "ai_can_approve_strategy": False,
            },
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_experiment": False,
            "can_modify_parameters": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_approve_strategy": False,
        }

    def report(self, report_id: str) -> dict[str, Any]:
        """Read one immutable report without invoking any agent or model."""
        return self.store.report(report_id)

    def reports(self, limit: int = 50) -> dict[str, Any]:
        """List persisted reports through the stable non-trading response contract."""
        return {
            "items": self.store.reports(limit),
            "can_trade": False,
            "can_create_orders": False,
            "used_for_execution": False,
        }

    def memories(self, limit: int = 100) -> dict[str, Any]:
        return {"items": self.store.memories(limit), "can_affect_execution": False}

    def questions(self, limit: int = 100) -> dict[str, Any]:
        return {"items": self.store.questions(limit), "can_launch_experiment": False}

    def _model_analysis(
        self,
        bundle: ResearchEvidenceBundle,
        results: tuple[AnalystResult, ...],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """Call DeepSeek through ModelService and reject any ungrounded content."""
        status = self.model_service.status()
        model_version = str(status.get("model") or "disabled")
        if status.get("state") != "connected":
            return model_version, None, {
                "grounded": False,
                "reason": "model_unavailable",
                "evidence_hash": bundle.evidence_hash,
            }
        try:
            completion = self.model_service.complete_json(
                MODEL_SYSTEM_PROMPT,
                {
                    "task": "summarize_quant_research_evidence",
                    "evidence": bundle.public_payload(),
                    "analyst_drafts": [item.as_dict() for item in results],
                    "required_capabilities": {
                        "can_trade": False,
                        "can_modify_strategy": False,
                        "can_approve_strategy": False,
                    },
                },
                temperature=0.0,
                max_tokens=2400,
            )
            normalized = self._normalize_model_content(completion.get("content"))
            evaluation = self.evaluator.evaluate_claims([
                {
                    "claim_id": "model-summary",
                    "label": "INFERENCE",
                    "title": "模型摘要",
                    "statement": normalized["summary"],
                    "evidence_ids": normalized["summary_evidence_ids"],
                    "metrics": {},
                },
                *normalized["conclusions"],
            ], bundle)
            if not evaluation["grounded"]:
                return str(completion.get("model") or model_version), None, evaluation
            return str(completion.get("model") or model_version), normalized, evaluation
        except Exception as exc:
            return model_version, None, {
                "grounded": False,
                "reason": f"{type(exc).__name__}: model_call_failed",
                "evidence_hash": bundle.evidence_hash,
            }

    @staticmethod
    def _normalize_model_content(value: Any) -> dict[str, Any]:
        """Bound model output size and shape before evaluation or persistence."""
        if not isinstance(value, Mapping):
            raise ValueError("模型输出必须是JSON对象")
        summary = str(value.get("summary") or "").strip()
        if not summary or len(summary) > 2000:
            raise ValueError("模型摘要为空或过长")
        summary_evidence_ids = [
            str(item) for item in value.get("summary_evidence_ids") or []
        ][:20]
        raw_claims = value.get("conclusions") or []
        if not isinstance(raw_claims, list) or len(raw_claims) > 20:
            raise ValueError("模型结论必须是有界数组")
        claims = []
        for index, item in enumerate(raw_claims):
            if not isinstance(item, Mapping):
                raise ValueError("模型结论结构无效")
            claims.append({
                "claim_id": str(item.get("claim_id") or f"model-{index + 1}")[:120],
                "label": str(item.get("label") or ""),
                "title": str(item.get("title") or "")[:200],
                "statement": str(item.get("statement") or "")[:2000],
                "evidence_ids": [str(value) for value in item.get("evidence_ids") or []][:20],
                "metrics": dict(item.get("metrics") or {}),
            })
        return {
            "summary": summary,
            "summary_evidence_ids": summary_evidence_ids,
            "conclusions": claims,
        }

    def _save_memory_and_questions(
        self,
        report: Mapping[str, Any],
        results: tuple[AnalystResult, ...],
    ) -> None:
        """Persist grounded observations and hypotheses outside all execution inputs."""
        type_map = {
            "strategy_analyst": "strategy_observation",
            "factor_analyst": "factor_observation",
            "risk_analyst": "error_lesson",
            "market_analyst": "market_pattern",
        }
        for result in results:
            for claim in result.as_dict()["claims"]:
                if claim["label"] in {"FACT", "INFERENCE"}:
                    self.store.add_memory(
                        memory_type=type_map[result.agent_type.value],
                        content=claim["statement"],
                        source_id=str(report["report_id"]),
                        source_claim_id=claim["claim_id"],
                        confidence=float(claim["confidence"]),
                        evidence_ids=list(claim["evidence_ids"]),
                    )
                elif claim["label"] == "HYPOTHESIS":
                    self.store.add_question(
                        question=claim["title"],
                        rationale=claim["statement"],
                        priority="HIGH" if result.agent_type.value == "risk_analyst" else "MEDIUM",
                        related_experiment=report.get("experiment_id"),
                        source_report_id=str(report["report_id"]),
                        source_claim_id=claim["claim_id"],
                        evidence_ids=list(claim["evidence_ids"]),
                    )
