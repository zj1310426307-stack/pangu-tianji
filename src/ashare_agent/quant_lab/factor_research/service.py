from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactStore
from ..contracts import ExperimentSpec, ExperimentState, ValidationStatus, sha256_json
from ..dataset_validator import DatasetValidator
from ..exceptions import DatasetBlockedError
from ..experiment_registry import ExperimentRegistry
from .ablation_engine import AblationEngine
from .contracts import FactorResearchConfig, FactorResearchResult
from .correlation_engine import CorrelationEngine
from .decay_engine import DecayEngine
from .factor_return import FactorPanelLoader
from .ic_engine import RankICEngine
from .quantile_engine import QuantileEngine
from .regime_engine import RegimeEngine
from .report_generator import FactorReportGenerator


class FactorResearchService:
    """Run seven descriptive analyses through Quant Lab lifecycle and artifacts only."""

    def __init__(self, project_root: Path, data_center_root: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.output_root = self.project_root / "output" / "quant_lab"
        self.data_center_root = Path(
            data_center_root or self.project_root / "output" / "data_center"
        )
        self.registry = ExperimentRegistry(self.output_root / "experiments.sqlite3")
        self.artifacts = ArtifactStore(self.output_root / "experiments")
        self.validator = DatasetValidator(self.data_center_root)
        self.loader = FactorPanelLoader(self.data_center_root)
        self.ic_engine = RankICEngine()
        self.quantile_engine = QuantileEngine()
        self.decay_engine = DecayEngine()
        self.correlation_engine = CorrelationEngine()
        self.ablation_engine = AblationEngine()
        self.regime_engine = RegimeEngine()
        self.report_generator = FactorReportGenerator()

    def run(
        self,
        spec: ExperimentSpec,
        *,
        config: FactorResearchConfig | None = None,
        trigger: str = "user_action",
    ) -> FactorResearchResult:
        """Validate Data Center evidence, compute analyses, register and seal every result."""
        settings = config or FactorResearchConfig()
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
            panel = self.loader.load(
                spec.data_center_run_ids,
                horizons=settings.horizons,
                data_start=spec.data_start,
                data_end=spec.data_end,
            )
            ic = self.ic_engine.run(panel.frame, settings)
            quantile_bundle = self.quantile_engine.run(panel.frame, settings)
            quantiles = quantile_bundle["quantiles"]
            factor_returns = quantile_bundle["factor_returns"]
            decay = self.decay_engine.run(ic, quantiles)
            correlation = self.correlation_engine.run(panel.frame, settings)
            ablation = self.ablation_engine.run(panel.frame, settings)
            stability = self.regime_engine.run(panel.frame, settings)
            metrics = self._metrics(
                ic, factor_returns, quantiles, decay, correlation, ablation, stability
            )
            metrics["engine_versions"] = self._engine_versions()
            report = self.report_generator.generate(
                spec=spec,
                run_id=run_id,
                config=settings,
                panel_manifest=panel.manifest,
                metrics=metrics,
                ic=ic,
                quantiles=quantiles,
                decay=decay,
                correlation=correlation,
                ablation=ablation,
                stability=stability,
            )
            self.registry.save_factor_research(
                run_id,
                metadata={
                    "experiment_id": spec.experiment_id,
                    "factor_version": spec.factor_version,
                    "period_start": spec.data_start,
                    "period_end": spec.data_end,
                    "horizons": list(settings.horizons),
                    "metrics_hash": sha256_json(metrics),
                    "dataset_version": spec.dataset_version,
                    "strategy_version": spec.strategy_version,
                    "dataset_label": spec.dataset_label,
                },
                report=report,
                factor_metrics=self._factor_rows(
                    ic, factor_returns, quantiles, correlation, spec, run_id
                ),
            )
            payloads = {
                "experiment_spec.json": asdict(spec),
                "factor_research_config.json": self._envelope(
                    spec,
                    run_id,
                    "factor_research_config",
                    {"config": asdict(settings), "engine_versions": self._engine_versions()},
                ),
                "factor_dataset_manifest.json": self._envelope(
                    spec, run_id, "factor_dataset_manifest", panel.manifest
                ),
                "validation_events.json": asdict(validation),
                "ic_analysis.json": self._envelope(spec, run_id, "ic_analysis", ic),
                "factor_return.json": self._envelope(
                    spec, run_id, "factor_return", factor_returns
                ),
                "quantile_analysis.json": self._envelope(
                    spec, run_id, "quantile_analysis", quantiles
                ),
                "decay_analysis.json": self._envelope(
                    spec, run_id, "decay_analysis", decay
                ),
                "factor_correlation.json": self._envelope(
                    spec, run_id, "factor_correlation", correlation
                ),
                "ablation.json": self._envelope(spec, run_id, "ablation", ablation),
                "stability.json": self._envelope(spec, run_id, "stability", stability),
                "metrics.json": self._envelope(spec, run_id, "factor_metrics", metrics),
                "factor_report.json": report,
            }
            artifacts = [
                self.artifacts.write_json(spec.experiment_id, run_id, name, payload)
                for name, payload in payloads.items()
            ]
            manifest = self.artifacts.finalize(spec.experiment_id, run_id, artifacts)
            self.registry.save_artifacts(run_id, artifacts + [manifest["manifest_artifact"]])
            result = FactorResearchResult(
                experiment_id=spec.experiment_id,
                run_id=run_id,
                dataset_version=spec.dataset_version,
                dataset_label=spec.dataset_label,
                strategy_version=spec.strategy_version,
                factor_version=spec.factor_version,
                factor_contract_hash=spec.factor_contract_hash,
                config_hash=settings.config_hash,
                metrics=metrics,
                factor_report=report,
                artifact_manifest=manifest,
                warnings=tuple(
                    event.message for event in validation.events
                    if event.status in {ValidationStatus.PARTIAL, ValidationStatus.UNAVAILABLE}
                ),
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
    def _metrics(
        ic: dict[str, Any],
        factor_returns: dict[str, Any],
        quantiles: dict[str, Any],
        decay: dict[str, Any],
        correlation: dict[str, Any],
        ablation: dict[str, Any],
        stability: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a compact registry/report summary without inventing validity conclusions."""
        ic_summary = {
            factor: {
                horizon: {
                    key: values.get(key)
                    for key in ("observation_count", "ic_mean", "ic_std", "icir", "ic_hit_rate", "t_stat", "p_value")
                }
                for horizon, values in horizons.items()
            }
            for factor, horizons in ic["factors"].items()
        }
        return {
            "ic": ic_summary,
            "factor_return_method": factor_returns["method"],
            "quantile_method": quantiles["method"],
            "decay_peak_horizons": {
                factor: values["peak_horizon"] for factor, values in decay["factors"].items()
            },
            "high_correlation_pairs": correlation.get("high_correlation_pairs", []),
            "redundancy_scores": correlation.get("redundancy_scores", {}),
            "ablation_contribution_order": ablation["contribution_order"],
            "stability_availability": stability["availability"],
            "investment_validity": "NOT_ESTABLISHED",
            "can_trade": False,
            "can_create_orders": False,
        }

    @staticmethod
    def _factor_rows(
        ic: dict[str, Any],
        factor_returns: dict[str, Any],
        quantiles: dict[str, Any],
        correlation: dict[str, Any],
        spec: ExperimentSpec,
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Flatten IC, return, risk, turnover and redundancy summaries for Registry queries."""
        rows: list[dict[str, Any]] = []
        for factor_name, horizons in ic["factors"].items():
            for horizon, values in horizons.items():
                returns = factor_returns["factors"][factor_name][horizon]
                quintile = quantiles["factors"][factor_name]["5"][horizon]["long_short"]
                rows.append({
                    "experiment_id": spec.experiment_id,
                    "run_id": run_id,
                    "factor_name": factor_name,
                    "factor_version": spec.factor_version,
                    "period": f"{spec.data_start}/{spec.data_end}",
                    "horizon": int(horizon),
                    "ic_mean": values["ic_mean"],
                    "ic_std": values["ic_std"],
                    "icir": values["icir"],
                    "ic_hit_rate": values["ic_hit_rate"],
                    "t_stat": values["t_stat"],
                    "p_value": values["p_value"],
                    "observation_count": values["observation_count"],
                    "annual_return": returns["annualized_return"],
                    "annual_volatility": returns["annualized_volatility"],
                    "max_drawdown": returns["max_drawdown"],
                    "turnover": returns["turnover"],
                    "long_short_return": quintile["mean_forward_return"],
                    "sharpe": returns["annualized_sharpe"],
                    "redundancy_score": correlation.get("redundancy_scores", {}).get(
                        factor_name
                    ),
                })
        return rows

    def _engine_versions(self) -> dict[str, str]:
        """Publish every orchestration component version into metrics and artifacts."""
        return {
            "ic": self.ic_engine.engine_version,
            "quantile": self.quantile_engine.engine_version,
            "decay": self.decay_engine.engine_version,
            "correlation": self.correlation_engine.engine_version,
            "ablation": self.ablation_engine.engine_version,
            "regime": self.regime_engine.engine_version,
            "report_generator": self.report_generator.generator_version,
        }

    @staticmethod
    def _envelope(
        spec: ExperimentSpec,
        run_id: str,
        artifact_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind every analysis artifact to the same experiment and safety identity."""
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
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
        }
