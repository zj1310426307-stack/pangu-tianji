from __future__ import annotations

from pathlib import Path
from typing import Any

from ..quant_ai.experiment_reader import QuantAIExperimentReader
from ..quant_lab.contracts import sha256_json, utc_now
from ..quant_lab.validation_gate import StrategyValidationService
from .artifacts import EvolutionArtifactStore
from .contracts import (
    EVOLUTION_SERVICE_VERSION,
    EvolutionArtifactError,
    EvolutionEvidenceError,
    EvolutionStateError,
    StrategyBranchSpec,
    safety_contract,
    stable_id,
)
from .evidence_reader import StrategyEvolutionEvidenceReader
from .evolution_report import StrategyEvolutionReportGenerator
from .lifecycle_manager import StrategyLifecycleManager
from .store import StrategyEvolutionStore
from .strategy_compare import StrategyComparisonEngine
from .strategy_monitor import StrategyHealthMonitor


class StrategyEvolutionService:
    """Orchestrate evidence-only strategy health, comparisons and human lifecycle."""

    service_version = EVOLUTION_SERVICE_VERSION

    def __init__(
        self,
        project_root: Path,
        *,
        strategy_validation_service: StrategyValidationService | None = None,
        evidence_reader: StrategyEvolutionEvidenceReader | None = None,
        store: StrategyEvolutionStore | None = None,
    ) -> None:
        self.root = Path(project_root)
        self.output = self.root / "output"
        self.validation = strategy_validation_service or StrategyValidationService(self.root)
        quant_reader = QuantAIExperimentReader(
            self.root,
            strategy_registry=self.validation.registry,
            experiment_registry=getattr(self.validation, "experiment_registry", None),
        )
        self.reader = evidence_reader or StrategyEvolutionEvidenceReader(quant_reader)
        self.store = store or StrategyEvolutionStore(self.output / "strategy_evolution.db")
        self.artifacts = EvolutionArtifactStore(
            self.output / "strategy_evolution" / "artifacts"
        )
        self.monitor = StrategyHealthMonitor()
        self.comparison_engine = StrategyComparisonEngine()
        self.lifecycle = StrategyLifecycleManager(self.store)
        self.report_generator = StrategyEvolutionReportGenerator()

    def import_branch(
        self,
        *,
        strategy_id: str,
        version: str,
        branch_name: str,
        parent_version: str | None = None,
    ) -> dict[str, Any]:
        """Import one immutable Validation Gate version as a non-executable branch."""
        version_record = self.validation.registry.version(strategy_id, version)
        strategy = self.validation.registry.strategy(strategy_id)
        if parent_version:
            self.validation.registry.version(strategy_id, parent_version)
        spec = dict(version_record["spec"])
        evidence_ids = [f"strategy_version:{version_record['version_hash']}"]
        branch = StrategyBranchSpec(
            strategy_id=strategy_id,
            strategy_name=str(strategy["name"]),
            version=version,
            branch_name=branch_name,
            parent_version=parent_version,
            version_hash=str(version_record["version_hash"]),
            code_hash=str(spec["code_hash"]),
            parameter_hash=str(spec["parameter_hash"]),
            factor_version=str(spec["factor_version"]),
            data_version=str(spec["dataset_version"]),
            dataset_label=str(spec["dataset_label"]),
            # Reviews and gate states evolve. They are observations, not branch ID.
            validation_state="REGISTERED",
            validation_review_id=None,
            evidence_ids=tuple(evidence_ids),
            created_at=str(
                version_record.get("created_at")
                or strategy.get("created_at")
                or "1970-01-01T00:00:00+00:00"
            ),
        )
        return {**self.store.register_branch(branch), "safety": safety_contract()}

    def evaluate(
        self,
        *,
        strategy_id: str,
        version: str,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate one sealed review idempotently and generate no strategy mutation."""
        try:
            branch = self.store.branch(strategy_id, version)
        except KeyError:
            branch = self.import_branch(
                strategy_id=strategy_id,
                version=version,
                branch_name="main",
            )
        selected_review = review_id or self._latest_review_id(strategy_id, version)
        observation = self.reader.read(selected_review)
        if (
            observation.strategy_id != strategy_id
            or observation.strategy_version != version
        ):
            raise EvolutionEvidenceError("策略观察与请求的策略版本不一致")
        evaluation_id = stable_id(
            "evolution-evaluation", strategy_id, version,
            observation.review_id, observation.evidence_hash,
        )
        health_id = stable_id("strategy-health", evaluation_id)
        try:
            existing = self.store.health(health_id)
            return self._evaluation_response(existing)
        except KeyError:
            pass
        baseline_health = self.store.latest_health(strategy_id, version)
        baseline = baseline_health.get("observation") if baseline_health else None
        monitored = self.monitor.evaluate(observation.as_dict(), baseline)
        created_at = utc_now()
        health = {
            "service_version": self.service_version,
            "health_id": health_id,
            "evaluation_id": evaluation_id,
            "strategy_id": strategy_id,
            "strategy_version": version,
            "review_id": observation.review_id,
            "observed_at": observation.observed_at,
            "created_at": created_at,
            "dataset_label": observation.dataset_label,
            "score": monitored["score"],
            "partial_score": monitored["partial_score"],
            "coverage": monitored["coverage"],
            "status": monitored["status"],
            "components": monitored["components"],
            "weights": monitored["weights"],
            "missing_components": monitored["missing_components"],
            "performance": monitored["performance"],
            "risk": monitored["risk"],
            "factor_drift": monitored["factor_drift"],
            "execution": monitored["execution"],
            "environment": monitored["environment"],
            "decay": monitored["decay"],
            "observation": observation.as_dict(),
            "evidence_ids": list(observation.evidence_ids),
            "evidence_hash": observation.evidence_hash,
            "data_gaps": list(observation.data_gaps),
            "baseline_health_id": baseline_health.get("health_id") if baseline_health else None,
            "diagnostic_only": True,
            "used_for_execution": False,
            **safety_contract(),
        }
        report = self.report_generator.generate(
            branch=branch,
            observation=observation.as_dict(),
            health=health,
        )
        health["report_id"] = report["report_id"]
        health["health_hash"] = sha256_json(health)
        manifest = self.artifacts.save(strategy_id, evaluation_id, report)
        saved = self.store.save_evaluation(health, report, manifest)
        return self._evaluation_response(saved)

    def observe_registered_strategies(self) -> dict[str, Any]:
        """Observe every active registered version using only its latest sealed review.

        Repeated daily runs remain idempotent because evaluation identity is derived
        from the review and evidence hash. Missing reviews are reported as gaps and
        never replaced with inferred metrics.
        """
        results: list[dict[str, Any]] = []
        gaps: list[dict[str, str]] = []
        strategies = self.validation.registry.list_strategies()
        if not strategies:
            gaps.append({
                "strategy_id": "none",
                "code": "NO_REGISTERED_STRATEGIES",
                "message": "Strategy Validation Gate尚无已登记策略版本",
            })
        for strategy in strategies:
            strategy_id = str(strategy.get("strategy_id") or "")
            version = str(strategy.get("active_version") or "")
            if not strategy_id or not version:
                gaps.append({
                    "strategy_id": strategy_id or "unknown",
                    "code": "ACTIVE_VERSION_UNAVAILABLE",
                })
                continue
            try:
                observed = self.evaluate(strategy_id=strategy_id, version=version)
            except (KeyError, EvolutionEvidenceError, EvolutionArtifactError) as exc:
                gaps.append({
                    "strategy_id": strategy_id,
                    "version": version,
                    "code": type(exc).__name__,
                    "message": str(exc),
                })
                continue
            results.append({
                "strategy_id": strategy_id,
                "version": version,
                "health_id": observed["health"]["health_id"],
                "status": observed["health"]["status"],
                "score": observed["health"].get("score"),
                "report_id": observed.get("report_id"),
            })
        return {
            "service_version": self.service_version,
            "observed_at": utc_now(),
            "results": results,
            "data_gaps": gaps,
            "counts": {"observed": len(results), "unavailable": len(gaps)},
            "used_for_execution": False,
            **safety_contract(),
        }

    def compare(
        self,
        *,
        strategy_id: str,
        version_a: str,
        version_b: str,
    ) -> dict[str, Any]:
        """Compare two registered versions using their latest sealed health snapshots."""
        if version_a == version_b:
            raise EvolutionEvidenceError("策略比较需要两个不同版本")
        self.store.branch(strategy_id, version_a)
        self.store.branch(strategy_id, version_b)
        left = self.store.latest_health(strategy_id, version_a)
        right = self.store.latest_health(strategy_id, version_b)
        if not left or not right:
            raise EvolutionEvidenceError("两个策略版本都必须先生成Strategy Health证据")
        return self.store.save_comparison(self.comparison_engine.compare(left, right))

    def request_transition(
        self,
        *,
        strategy_id: str,
        version: str,
        target_state: str,
        requested_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Request the next lifecycle state after deterministic eligibility checks."""
        branch = self.store.branch(strategy_id, version)
        eligibility = self._eligibility(branch, target_state)
        return self.lifecycle.request(
            strategy_id=strategy_id,
            version=version,
            target_state=target_state,
            requested_by=requested_by,
            reason=reason,
            eligibility=eligibility,
        )

    def decide_transition(
        self,
        request_id: str,
        *,
        approved: bool,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Record a human lifecycle decision that never changes production execution."""
        result = self.lifecycle.decide(
            request_id, approved=approved, actor=actor, reason=reason
        )
        return {**result, "production_strategy_changed": False, **safety_contract()}

    def dashboard(self) -> dict[str, Any]:
        """Return the backend-owned Strategy Evolution Center snapshot."""
        branches = self.store.branches()
        health = self.store.health_items(limit=100)
        comparisons = self.store.comparisons(limit=50)
        transitions = self.store.transitions(limit=100)
        reports = self.store.reports(limit=50)
        sources = self.validation.registry.list_strategies()
        latest_by_version: dict[str, dict[str, Any]] = {}
        for item in health:
            key = f"{item['strategy_id']}::{item['strategy_version']}"
            latest_by_version.setdefault(key, item)
        for branch in branches:
            key = f"{branch['strategy_id']}::{branch['version']}"
            branch["latest_health"] = latest_by_version.get(key)
            branch["next_state"] = self.lifecycle.next_state(branch["lifecycle_state"])
            branch["validation_state_current"] = self._validation_state(
                branch["strategy_id"]
            )
        return {
            "service_version": self.service_version,
            "generated_at": utc_now(),
            "branches": branches,
            "health_history": health,
            "comparisons": comparisons,
            "lifecycle_history": transitions,
            "reports": reports,
            "available_validation_strategies": sources,
            "counts": {
                "branches": len(branches),
                "health_snapshots": len(health),
                "decaying": sum(item.get("status") in {"DECAYING", "CRITICAL"} for item in health),
                "comparisons": len(comparisons),
                "pending_transitions": sum(item.get("status") == "PENDING" for item in transitions),
            },
            "ai_strategy_observer": {
                "interface": "strategy-observer-readonly-v1.0.0",
                "can_propose_research_questions": True,
                "can_launch_experiment": False,
                "can_modify_strategy": False,
                "can_approve_lifecycle": False,
            },
            "safety": {
                **safety_contract(),
                "production_candidate_is_live_trading": False,
                "human_approval_required": True,
            },
            **safety_contract(),
        }

    def health(self, strategy_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List immutable health snapshots without running a new evaluation."""
        items = self.store.health_items(strategy_id, limit)
        return {"items": items, "count": len(items), "safety": safety_contract()}

    def report(self, report_id: str) -> dict[str, Any]:
        """Read and verify both database and filesystem report evidence."""
        report = self.store.report(report_id)
        evaluation_id = stable_id(
            "evolution-evaluation", report["strategy_id"], report["strategy_version"],
            (report.get("sections") or {}).get("02_evidence_scope", {}).get("review_id"),
            report.get("evidence_hash"),
        )
        artifact = self.artifacts.read(report["strategy_id"], evaluation_id)
        if artifact["report"] != report:
            raise EvolutionEvidenceError("数据库与artifact中的Strategy Health Report不一致")
        return {**report, "artifact_manifest": artifact["manifest"]}

    def comparisons(self, limit: int = 100) -> dict[str, Any]:
        """List persisted comparisons without selecting an automatic winner."""
        items = self.store.comparisons(limit=max(1, min(int(limit), 500)))
        return {"items": items, "count": len(items), "safety": safety_contract()}

    def lifecycle_history(self, limit: int = 100) -> dict[str, Any]:
        """List human lifecycle requests and decisions without changing state."""
        items = self.store.transitions(limit=max(1, min(int(limit), 500)))
        return {"items": items, "count": len(items), "safety": safety_contract()}

    def _latest_review_id(self, strategy_id: str, version: str) -> str:
        """Select the newest validation review for one exact strategy version."""
        reviews = self.validation.registry.list_reviews(strategy_id, limit=200)
        for review in reviews:
            if review.get("strategy_version") == version:
                return str(review["review_id"])
        raise EvolutionEvidenceError("该策略版本尚无Strategy Validation审查")

    def _eligibility(self, branch: dict[str, Any], target_state: str) -> dict[str, Any]:
        """Build deterministic lifecycle eligibility without mutating upstream state."""
        evidence_ids = list(branch.get("evidence_ids") or [])
        reasons: list[str] = []
        current = branch["lifecycle_state"]
        health_items = self.store.health_items(branch["strategy_id"], 300)
        version_health = [
            item for item in health_items
            if item["strategy_version"] == branch["version"]
        ]
        latest = version_health[0] if version_health else None
        validation_state = self._validation_state(branch["strategy_id"])
        if target_state == "RESEARCH":
            pass
        elif target_state == "VALIDATED":
            if validation_state not in {"OUT_OF_SAMPLE", "PAPER_TRADING"}:
                reasons.append("Validation Gate尚未达到OUT_OF_SAMPLE")
        elif target_state == "PAPER_RUNNING":
            if validation_state != "PAPER_TRADING":
                reasons.append("Validation Gate尚未达到PAPER_TRADING")
            if branch["dataset_label"] != "POINT_IN_TIME":
                reasons.append("合成数据不能进入PAPER_RUNNING")
        elif target_state == "PRODUCTION_CANDIDATE":
            if len(version_health) < 2:
                reasons.append("至少需要两个独立封存时点的Strategy Health证据")
            if not latest or latest.get("score") is None or float(latest["score"]) < 70:
                reasons.append("最新Strategy Health Score不足70")
            if not latest or float(latest.get("coverage") or 0) < 0.80:
                reasons.append("最新Strategy Health证据覆盖不足80%")
            if latest and latest.get("status") in {"DECAYING", "CRITICAL"}:
                reasons.append("策略处于衰减或严重状态")
            if branch["dataset_label"] != "POINT_IN_TIME":
                reasons.append("合成数据不能成为PRODUCTION_CANDIDATE")
        elif target_state in {"DEPRECATED", "RETIRED"}:
            pass
        else:
            reasons.append(f"未知目标状态：{target_state}")
        if latest:
            evidence_ids.extend(latest.get("evidence_ids") or [])
            evidence_ids.append(f"strategy_health:{latest['health_id']}:{latest['health_hash']}")
        return {
            "eligible": not reasons,
            "current_state": current,
            "target_state": target_state,
            "block_reasons": reasons,
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "validation_state_current": validation_state,
        }

    def _validation_state(self, strategy_id: str) -> str:
        """Read the current upstream gate state without mutating branch identity."""
        return str(self.validation.registry.strategy(strategy_id)["current_state"])

    @staticmethod
    def _evaluation_response(health: dict[str, Any]) -> dict[str, Any]:
        """Return one stable service envelope with all capabilities disabled."""
        return {
            "service_version": EVOLUTION_SERVICE_VERSION,
            "health": health,
            "report_id": health.get("report_id"),
            "strategy_changed": False,
            "parameters_changed": False,
            "production_strategy_replaced": False,
            "safety": safety_contract(),
            **safety_contract(),
        }
