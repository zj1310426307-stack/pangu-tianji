from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping

from .artifacts import ArtifactStore
from .benchmark import BenchmarkResult
from .contracts import (
    ExperimentResult,
    ExperimentSpec,
    ExperimentState,
    StrategyLifecycleState,
    utc_now,
)
from .dataset_validator import DatasetValidator
from .exceptions import DatasetBlockedError
from .experiment_registry import ExperimentRegistry
from .splitters import SplitManifest


class QuantLabService:
    """Orchestrate validation, existing backtests, immutable artifacts and registry state.

    This service has no order, broker, portfolio or risk mutation dependency. Its
    only executable callback is a caller-supplied historical backtest adapter.
    """

    def __init__(self, project_root: Path, data_center_root: Path | None = None) -> None:
        self.project_root = project_root
        self.output_root = project_root / "output" / "quant_lab"
        self.registry = ExperimentRegistry(self.output_root / "experiments.sqlite3")
        self.artifacts = ArtifactStore(self.output_root / "experiments")
        self.validator = DatasetValidator(
            data_center_root or project_root / "output" / "data_center"
        )

    def run_experiment(
        self,
        spec: ExperimentSpec,
        *,
        split_manifest: SplitManifest,
        benchmark: BenchmarkResult,
        backtest_runner: Callable[[], Any],
        trigger: str = "user_action",
    ) -> ExperimentResult:
        """Execute one fail-closed experiment without granting any trading ability."""
        self.registry.register(spec)
        run_id = self.registry.create_run(spec.experiment_id, trigger=trigger)
        created_at = self.registry.run(run_id)["created_at"]
        try:
            self.registry.transition(run_id, ExperimentState.VALIDATING)
            validation = self.validator.validate(spec)
            self.registry.save_validation(run_id, validation)
            if not validation.can_run:
                reasons = tuple(
                    event.message for event in validation.events if event.status.value == "BLOCKED"
                )
                self.registry.transition(
                    run_id,
                    ExperimentState.DATA_BLOCKED,
                    error_code="DATASET_VALIDATION_BLOCKED",
                    error_message="；".join(reasons),
                )
                raise DatasetBlockedError("数据验证未通过：" + "；".join(reasons))
            self.registry.transition(run_id, ExperimentState.RUNNING)
            started_at = self.registry.run(run_id)["started_at"]
            backtest = backtest_runner()
            backtest_payload = self._backtest_payload(backtest)
            artifacts: list[dict[str, Any]] = []
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "experiment_spec.json", asdict(spec)
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "dataset_manifest.json",
                self._dataset_manifest(spec),
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "split_manifest.json", asdict(split_manifest)
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "strategy_manifest.json",
                {
                    "strategy_version": spec.strategy_version,
                    "factor_version": spec.factor_version,
                    "factor_contract_hash": spec.factor_contract_hash,
                    "code_hash": spec.code_hash,
                    "code_commit": spec.code_commit,
                    "uses_shared_research_pipeline": True,
                    "strategy_state": StrategyLifecycleState.DATA_VERIFIED.value,
                },
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "benchmark_manifest.json", asdict(benchmark)
            ))
            cost_model = {
                "version": spec.cost_model_version,
                "config": dict(spec.config.get("cost_model") or {}),
            }
            cost_model["config_hash"] = hashlib.sha256(
                json.dumps(cost_model["config"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "cost_model.json", cost_model
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "validation_events.json", asdict(validation)
            ))
            artifacts.append(self.artifacts.write_json(
                spec.experiment_id, run_id, "metrics.json", backtest_payload.get("summary", {})
            ))
            for name, key in (
                ("equity_curve.json", "equity_records"),
                ("trades.json", "trade_records"),
                ("rankings.json", "ranking_records"),
            ):
                if backtest_payload.get(key):
                    artifacts.append(self.artifacts.write_json(
                        spec.experiment_id, run_id, name, backtest_payload[key]
                    ))
            manifest = self.artifacts.finalize(spec.experiment_id, run_id, artifacts)
            completed_at = utc_now()
            warnings = tuple(
                event.message for event in validation.events
                if event.status.value in {"PARTIAL", "UNAVAILABLE"}
            )
            result = ExperimentResult(
                experiment_id=spec.experiment_id,
                run_id=run_id,
                state=ExperimentState.COMPLETED,
                strategy_state=StrategyLifecycleState.DATA_VERIFIED,
                created_at=created_at,
                started_at=started_at,
                completed_at=completed_at,
                dataset_summary={
                    "dataset_version": spec.dataset_version,
                    "dataset_label": spec.dataset_label,
                    "run_ids": list(spec.data_center_run_ids),
                    "validation_hash": validation.validation_hash,
                },
                split_summary={"manifest_hash": split_manifest.manifest_hash,
                               "fold_count": len(split_manifest.folds)},
                backtest_summary=backtest_payload.get("summary", {}),
                benchmark_summary={
                    "benchmark_id": benchmark.contract.benchmark_id,
                    "version": benchmark.contract.version,
                    "availability": benchmark.availability,
                    "benchmark_hash": benchmark.benchmark_hash,
                },
                artifact_manifest=manifest,
                warnings=warnings,
            )
            self.registry.save_artifacts(
                run_id, artifacts + [manifest["manifest_artifact"]]
            )
            self.registry.transition(
                run_id, ExperimentState.COMPLETED, result_hash=result.result_hash
            )
            return result
        except DatasetBlockedError:
            raise
        except Exception as exc:
            state = ExperimentState(self.registry.run(run_id)["state"])
            if state not in {
                ExperimentState.COMPLETED,
                ExperimentState.FAILED,
                ExperimentState.DATA_BLOCKED,
            }:
                self.registry.transition(
                    run_id,
                    ExperimentState.FAILED,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            raise

    def _dataset_manifest(self, spec: ExperimentSpec) -> dict[str, Any]:
        """Snapshot Data Center manifest identities without copying source datasets."""
        manifests: list[dict[str, Any]] = []
        for run_id in spec.data_center_run_ids:
            path = self.validator.root
            with sqlite3.connect(path / "catalog.sqlite3") as connection:
                row = connection.execute(
                    "SELECT manifest_path,verified FROM research_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if row is None:
                continue
            manifest_path = path / str(row[0])
            content = manifest_path.read_bytes()
            manifest = json.loads(content.decode("utf-8"))
            manifests.append({
                "run_id": run_id,
                "verified": bool(row[1]),
                "manifest_sha256": hashlib.sha256(content).hexdigest(),
                "data_version": manifest.get("data_version"),
                "assets": {
                    name: item.get("sha256") for name, item in manifest.get("assets", {}).items()
                },
            })
        return {
            "dataset_version": spec.dataset_version,
            "dataset_label": spec.dataset_label,
            "data_start": spec.data_start,
            "data_end": spec.data_end,
            "runs": manifests,
        }

    @staticmethod
    def _backtest_payload(backtest: Any) -> dict[str, Any]:
        """Accept the official adapter contract or a strictly data-only mapping for tests."""
        if hasattr(backtest, "__dataclass_fields__"):
            return asdict(backtest)
        if isinstance(backtest, Mapping):
            return dict(backtest)
        raise TypeError("backtest_runner必须返回BacktestAdapterResult或只读mapping")
