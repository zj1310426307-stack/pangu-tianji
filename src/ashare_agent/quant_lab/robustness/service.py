from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactStore
from ..contracts import ExperimentSpec, ExperimentState, ValidationStatus, sha256_json
from ..dataset_validator import DatasetValidator
from ..exceptions import ContractError, DatasetBlockedError
from ..experiment_registry import ExperimentRegistry
from .bootstrap import analyze_bootstrap
from .contracts import RobustnessConfig, RobustnessResult, ScenarioRunner, StrategyPath
from .cost_stress import analyze_cost_stress
from .delay_stress import analyze_delay_stress
from .overfitting import analyze_overfitting
from .parameter_sensitivity import analyze_parameter_sensitivity
from .regime_test import analyze_regimes
from .robustness_report import RobustnessReportGenerator
from .robustness_score import calculate_robustness_score
from .rolling_window import analyze_rolling_windows


class RobustnessService:
    """Orchestrate read-only stress tests through Quant Lab lifecycle and evidence stores."""

    service_version = "strategy-robustness-service-v1.0.0"

    def __init__(self, project_root: Path, data_center_root: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.output_root = self.project_root / "output" / "quant_lab"
        self.data_center_root = Path(
            data_center_root or self.project_root / "output" / "data_center"
        )
        self.registry = ExperimentRegistry(self.output_root / "experiments.sqlite3")
        self.artifacts = ArtifactStore(self.output_root / "experiments")
        self.validator = DatasetValidator(self.data_center_root)
        self.report_generator = RobustnessReportGenerator()

    def run(
        self,
        spec: ExperimentSpec,
        *,
        scenario_runner: ScenarioRunner,
        config: RobustnessConfig | None = None,
        trigger: str = "user_action",
    ) -> RobustnessResult:
        """Validate evidence, execute every declared scenario, analyze and seal results."""
        settings = config or RobustnessConfig()
        self.registry.register(spec)
        run_id = self.registry.create_run(spec.experiment_id, trigger=trigger)
        try:
            self.registry.transition(run_id, ExperimentState.VALIDATING)
            validation = self.validator.validate(spec)
            self.registry.save_validation(run_id, validation)
            if not validation.can_run:
                reasons = tuple(
                    event.message for event in validation.events
                    if event.status == ValidationStatus.BLOCKED
                )
                self.registry.transition(
                    run_id,
                    ExperimentState.DATA_BLOCKED,
                    error_code="DATASET_VALIDATION_BLOCKED",
                    error_message="；".join(reasons),
                )
                raise DatasetBlockedError("数据验证未通过：" + "；".join(reasons))
            self.registry.transition(run_id, ExperimentState.RUNNING)
            paths = self._run_scenarios(spec, settings, scenario_runner)
            scenario_manifest = self._scenario_manifest(spec, paths)
            parameter = analyze_parameter_sensitivity(paths, settings)
            cost = analyze_cost_stress(paths["baseline"], settings)
            delay = analyze_delay_stress(paths, settings)
            regime = analyze_regimes(paths["baseline"], settings)
            bootstrap = analyze_bootstrap(paths["baseline"], settings)
            rolling = analyze_rolling_windows(paths["baseline"], settings)
            overfitting = analyze_overfitting(paths, settings)
            score = calculate_robustness_score(
                parameter=parameter,
                cost=cost,
                regime=regime,
                bootstrap=bootstrap,
                rolling=rolling,
                overfitting=overfitting,
                config=settings,
                out_of_sample_evidence=(
                    scenario_manifest["evaluation_partition"] == "final_holdout"
                ),
            )
            metrics = self._metrics(
                parameter, cost, delay, regime, bootstrap, rolling, overfitting, score
            )
            report = self.report_generator.generate(
                spec=spec,
                run_id=run_id,
                config=settings,
                scenario_manifest=scenario_manifest,
                parameter=parameter,
                cost=cost,
                delay=delay,
                regime=regime,
                bootstrap=bootstrap,
                rolling=rolling,
                overfitting=overfitting,
                score=score,
            )
            self.registry.save_robustness(
                run_id,
                metadata={
                    "experiment_id": spec.experiment_id,
                    "period_start": spec.data_start,
                    "period_end": spec.data_end,
                    "config_hash": settings.config_hash,
                    "metrics_hash": sha256_json(metrics),
                    "dataset_version": spec.dataset_version,
                    "strategy_version": spec.strategy_version,
                    "dataset_label": spec.dataset_label,
                },
                report=report,
                robustness_metrics=self._registry_rows(
                    parameter, cost, delay, regime, bootstrap, rolling, overfitting, score
                ),
            )
            payloads = {
                "experiment_spec.json": asdict(spec),
                "robustness_config.json": self._envelope(
                    spec, run_id, "robustness_config", asdict(settings)
                ),
                "validation_events.json": asdict(validation),
                "scenario_manifest.json": self._envelope(
                    spec, run_id, "scenario_manifest", scenario_manifest
                ),
                "strategy_paths.json": self._envelope(
                    spec, run_id, "strategy_paths", {
                        name: asdict(path) for name, path in sorted(paths.items())
                    }
                ),
                "parameter_sensitivity.json": self._envelope(
                    spec, run_id, "parameter_sensitivity", parameter
                ),
                "cost_stress.json": self._envelope(spec, run_id, "cost_stress", cost),
                "delay_stress.json": self._envelope(spec, run_id, "delay_stress", delay),
                "regime_test.json": self._envelope(spec, run_id, "regime_test", regime),
                "bootstrap.json": self._envelope(spec, run_id, "bootstrap", bootstrap),
                "rolling_analysis.json": self._envelope(
                    spec, run_id, "rolling_analysis", rolling
                ),
                "overfitting.json": self._envelope(spec, run_id, "overfitting", overfitting),
                "robustness_score.json": self._envelope(
                    spec, run_id, "robustness_score", score
                ),
                "metrics.json": self._envelope(spec, run_id, "robustness_metrics", metrics),
                "robustness_report.json": report,
            }
            artifacts = [
                self.artifacts.write_json(spec.experiment_id, run_id, name, payload)
                for name, payload in payloads.items()
            ]
            manifest = self.artifacts.finalize(spec.experiment_id, run_id, artifacts)
            self.registry.save_artifacts(run_id, artifacts + [manifest["manifest_artifact"]])
            warnings = tuple(
                event.message for event in validation.events
                if event.status in {ValidationStatus.PARTIAL, ValidationStatus.UNAVAILABLE}
            )
            result = RobustnessResult(
                experiment_id=spec.experiment_id,
                run_id=run_id,
                dataset_version=spec.dataset_version,
                dataset_label=spec.dataset_label,
                config_hash=settings.config_hash,
                metrics=metrics,
                report=report,
                artifact_manifest=manifest,
                warnings=warnings,
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

    @staticmethod
    def _run_scenarios(
        spec: ExperimentSpec, config: RobustnessConfig, scenario_runner: ScenarioRunner
    ) -> dict[str, StrategyPath]:
        """Run the fixed scenario list and enforce identity plus date alignment."""
        paths: dict[str, StrategyPath] = {}
        for request in config.scenario_requests():
            path = scenario_runner(request)
            if path.scenario_id != request.scenario_id:
                raise ContractError("scenario_runner返回的scenario_id与请求不一致")
            paths[request.scenario_id] = path
        baseline_dates = paths["baseline"].dates
        if min(baseline_dates) < spec.data_start or max(baseline_dates) > spec.data_end:
            raise ContractError("稳健性策略路径超出ExperimentSpec数据范围")
        for path in paths.values():
            if path.dates != baseline_dates:
                raise ContractError("所有稳健性场景必须使用相同历史日期轴")
        return paths

    def _scenario_manifest(
        self, spec: ExperimentSpec, paths: Mapping[str, StrategyPath]
    ) -> dict[str, Any]:
        """Persist scenario lineage without confusing path identity with experiment identity."""
        final_holdout = spec.split.final_holdout
        dates = paths["baseline"].dates
        is_final_holdout = bool(
            final_holdout
            and min(dates) >= final_holdout.start
            and max(dates) <= final_holdout.end
        )
        return {
            "service_version": self.service_version,
            "scenario_count": len(paths),
            "scenario_policy": "predeclared_no_best_selection",
            "evaluation_partition": "final_holdout" if is_final_holdout else "mixed_or_non_holdout",
            "data_center_run_ids": list(spec.data_center_run_ids),
            "engine_versions": sorted({path.engine_version for path in paths.values()}),
            "scenarios": [
                {
                    "scenario_id": name,
                    "path_hash": path.path_hash,
                    "source_result_hash": path.result_hash,
                    "engine_version": path.engine_version,
                    "observation_count": len(path.dates),
                }
                for name, path in sorted(paths.items())
            ],
        }

    def _metrics(
        self,
        parameter: dict,
        cost: dict,
        delay: dict,
        regime: dict,
        bootstrap: dict,
        rolling: dict,
        overfitting: dict,
        score: dict,
    ) -> dict[str, Any]:
        """Publish compact availability and health evidence without an admission decision."""
        return {
            "service_version": self.service_version,
            "report_generator_version": self.report_generator.generator_version,
            "baseline": parameter["baseline"],
            "parameter_stability_score": parameter.get("stability_score"),
            "cost_resilience_score": cost.get("resilience_score"),
            "delay_stability_score": delay.get("stability_score"),
            "regime_availability": regime.get("availability"),
            "bootstrap_availability": bootstrap.get("availability"),
            "rolling_availability": rolling.get("availability"),
            "overfitting_availability": overfitting.get("availability"),
            "robustness_score": score,
            "investment_validity": "NOT_ESTABLISHED",
            "strategy_admission_decision": "NOT_EVALUATED",
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _registry_rows(
        parameter: dict,
        cost: dict,
        delay: dict,
        regime: dict,
        bootstrap: dict,
        rolling: dict,
        overfitting: dict,
        score: dict,
    ) -> list[dict[str, Any]]:
        """Flatten queryable summaries while keeping high-dimensional evidence in artifacts."""
        rows: list[dict[str, Any]] = []
        def add(group: str, scenario: str, payload: dict, *, stability=None, cost_impact=None) -> None:
            """Store only scalar summaries; distributions remain authoritative artifacts."""
            metrics = payload.get("metrics", payload)
            def scalar(name: str) -> float | None:
                value = metrics.get(name)
                return float(value) if isinstance(value, (int, float)) else None
            rows.append({
                "metric_group": group,
                "scenario_id": scenario,
                "availability": payload.get("availability", "AVAILABLE"),
                "annual_return": scalar("annualized_return"),
                "max_drawdown": scalar("max_drawdown"),
                "sharpe": scalar("sharpe"),
                "cost_impact": cost_impact,
                "stability_score": stability,
                "payload_hash": sha256_json(payload),
            })
        add("baseline", "baseline", parameter["baseline"])
        for item in parameter["scenarios"]:
            add("parameter", item["scenario_id"], item, stability=item["stability_score"])
        for item in cost["scenarios"]:
            add("cost", item["scenario_id"], item, cost_impact=item["annualized_return_impact"])
        for item in delay["scenarios"]:
            add("delay", item["scenario_id"], item)
        for name, item in regime.get("regimes", {}).items():
            add("regime", name, item)
        add("bootstrap", "distribution", bootstrap)
        add("rolling", "summary", rolling, stability=rolling.get("stability_score"))
        add("overfitting", "summary", overfitting)
        add("score", "robustness", score, stability=score.get("score"))
        return rows

    @staticmethod
    def _envelope(
        spec: ExperimentSpec, run_id: str, artifact_type: str, payload: Any
    ) -> dict[str, Any]:
        """Bind every robustness artifact to immutable experiment and safety identities."""
        return {
            "artifact_type": artifact_type,
            "experiment_id": spec.experiment_id,
            "run_id": run_id,
            "data_version": spec.dataset_version,
            "dataset_label": spec.dataset_label,
            "strategy_version": spec.strategy_version,
            "factor_version": spec.factor_version,
            "factor_contract_hash": spec.factor_contract_hash,
            "payload": payload,
            "investment_validity": "NOT_ESTABLISHED",
            "strategy_admission_decision": "NOT_EVALUATED",
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_promote_strategy": False,
        }
