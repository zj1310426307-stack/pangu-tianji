from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactStore
from ..contracts import sha256_json
from ..exceptions import ContractError
from ..experiment_registry import ExperimentRegistry
from .approval_workflow import ApprovalWorkflow
from .contracts import (
    StrategyState,
    StrategyVersionSpec,
    ValidationEvidenceBundle,
    ValidationGateConfig,
)
from .gate_engine import StrategyValidationGate
from .review_report import ResearchCommitteeReport
from .strategy_registry import StrategyRegistry


class StrategyValidationService:
    """Orchestrate evidence review, sealed artifacts and human approval governance.

    This service has no dependency on brokers, order managers, paper portfolios,
    production strategies, factor weights, or live-trading configuration.
    """

    service_version = "strategy-validation-service-v1.0.0"

    def __init__(
        self,
        project_root: Path,
        *,
        config: ValidationGateConfig | None = None,
        experiment_registry: ExperimentRegistry | None = None,
    ) -> None:
        self.project_root = project_root
        self.output_root = project_root / "output" / "quant_lab"
        # This Windows runtime can open the existing project SQLite file but
        # cannot create a new SQLite file below the non-ASCII real path. Keep
        # the validation tables namespaced in the existing local database.
        self.registry = StrategyRegistry(project_root / "output" / "agent.db")
        self.experiment_registry = experiment_registry
        self.artifacts = ArtifactStore(self.output_root / "strategy_reviews")
        self.gate = StrategyValidationGate(config=config)
        self.report_generator = ResearchCommitteeReport()
        self.approvals = ApprovalWorkflow(self.registry, self.gate.policy)

    def register(self, strategy: StrategyVersionSpec) -> dict[str, Any]:
        """Register one immutable strategy version in DRAFT state."""
        return self.registry.register(strategy)

    def evidence_from_quant_lab(
        self,
        *,
        strategy: StrategyVersionSpec,
        data_integrity: Mapping[str, Any],
        factor_run_id: str | None,
        robustness_run_id: str | None,
        out_of_sample: Mapping[str, Any] | None = None,
        paper_trading: Mapping[str, Any] | None = None,
    ) -> ValidationEvidenceBundle:
        """Read and hash 008-B/C registered reports rather than trusting UI summaries."""
        experiment_registry = self.experiment_registry
        if experiment_registry is None:
            path = self.output_root / "experiments.sqlite3"
            if not path.is_file():
                raise ContractError("Quant Lab实验Registry尚不存在，无法读取008-B/C证据")
            experiment_registry = ExperimentRegistry(path)
        factor = experiment_registry.factor_report(factor_run_id) if factor_run_id else None
        robust = (
            experiment_registry.robustness_report(robustness_run_id)
            if robustness_run_id else None
        )
        for label, report in (("factor", factor), ("robustness", robust)):
            if report and report.get("strategy_version") != strategy.version:
                raise ContractError(f"{label}报告strategy_version与准入策略不一致")
            if report and report.get("factor_version") != strategy.factor_version:
                raise ContractError(f"{label}报告factor_version与准入策略不一致")
        evidence_ids = [
            f"strategy_version:{strategy.version_hash}",
            f"data_integrity:{sha256_json(data_integrity)}",
        ]
        if factor:
            evidence_ids.append(f"factor_report:{factor_run_id}:{sha256_json(factor)}")
        if robust:
            evidence_ids.append(f"robustness_report:{robustness_run_id}:{sha256_json(robust)}")
        if out_of_sample:
            evidence_ids.append(f"out_of_sample:{sha256_json(out_of_sample)}")
        if paper_trading:
            evidence_ids.append(f"paper_trading:{sha256_json(paper_trading)}")
        return ValidationEvidenceBundle(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            data_integrity=dict(data_integrity),
            factor_report=factor,
            robustness_report=robust,
            out_of_sample=dict(out_of_sample) if out_of_sample else None,
            paper_trading=dict(paper_trading) if paper_trading else None,
            evidence_ids=tuple(evidence_ids),
        )

    def review(
        self,
        *,
        strategy: StrategyVersionSpec,
        evidence: ValidationEvidenceBundle,
    ) -> dict[str, Any]:
        """Seal one non-binding review; never create an approval or change strategy state."""
        self.registry.register(strategy)
        if (evidence.strategy_id, evidence.strategy_version) != (
            strategy.strategy_id, strategy.version
        ):
            raise ContractError("准入证据与策略版本身份不一致")
        current = StrategyState(self.registry.strategy(strategy.strategy_id)["current_state"])
        review = self.gate.review(current_state=current, evidence=evidence)
        report = self.report_generator.generate(strategy=strategy, review=review)
        payloads = {
            "evidence_bundle.json": {
                "schema_version": "strategy-validation-evidence-v1.0.0",
                "strategy_id": strategy.strategy_id,
                "strategy_version": strategy.version,
                "evidence_hash": evidence.evidence_hash,
                "evidence": asdict(evidence),
                "can_trade": False,
                "can_create_orders": False,
            },
            "validation_review.json": review.as_dict(),
            "committee_report.json": report,
        }
        artifacts = [
            self.artifacts.write_json(strategy.strategy_id, review.review_id, name, payload)
            for name, payload in payloads.items()
        ]
        manifest = self.artifacts.finalize(strategy.strategy_id, review.review_id, artifacts)
        self.registry.save_review(review, report, manifest)
        return {
            "service_version": self.service_version,
            "review": review.as_dict(),
            "report": report,
            "artifact_manifest": manifest,
            "approval_created": False,
            "strategy_state_changed": False,
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_auto_promote": False,
        }

    def dashboard(self) -> dict[str, Any]:
        """Expose a read-only Strategy Lab snapshot for API and UI adapters."""
        strategies = self.registry.list_strategies()
        reviews = self.registry.list_reviews(limit=100)
        promotions = self.registry.list_promotions(limit=100)
        return {
            "service_version": self.service_version,
            "gate_contract": self.gate.describe(),
            "strategies": strategies,
            "reviews": reviews,
            "promotion_history": promotions,
            "counts": {
                "strategies": len(strategies),
                "reviews": len(reviews),
                "pending_approvals": sum(
                    item["approval_status"] == "PENDING" for item in promotions
                ),
            },
            "safety": {
                "human_approval_required": True,
                "ai_can_approve": False,
                "paper_is_not_live": True,
                "can_modify_production_strategy": False,
            },
            "can_trade": False,
            "can_create_orders": False,
            "can_auto_promote": False,
        }
