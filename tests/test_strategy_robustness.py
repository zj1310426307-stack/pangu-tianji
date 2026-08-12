from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from ashare_agent.quant_lab.contracts import (
    DatasetValidation,
    DateRange,
    ExperimentSpec,
    SplitDefinition,
    ValidationEvent,
    ValidationStatus,
    sha256_json,
)
from ashare_agent.quant_lab.exceptions import ContractError
from ashare_agent.quant_lab.robustness.bootstrap import analyze_bootstrap
from ashare_agent.quant_lab.robustness.contracts import (
    RobustnessConfig,
    ScenarioRequest,
    StrategyPath,
)
from ashare_agent.quant_lab.robustness.cost_stress import analyze_cost_stress
from ashare_agent.quant_lab.robustness.delay_stress import analyze_delay_stress
from ashare_agent.quant_lab.robustness.overfitting import analyze_overfitting
from ashare_agent.quant_lab.robustness.parameter_sensitivity import analyze_parameter_sensitivity
from ashare_agent.quant_lab.robustness.regime_test import analyze_regimes
from ashare_agent.quant_lab.robustness.robustness_score import calculate_robustness_score
from ashare_agent.quant_lab.robustness.rolling_window import analyze_rolling_windows
from ashare_agent.quant_lab.robustness.service import RobustnessService


PROTECTED_HASHES = {
    "src/ashare_agent/research_pipeline.py": "1FD79ED464E75EE1D6F1477AB7267178FD00D9329AA8EFC311C5B57058678577",
    "src/ashare_agent/factor_model.py": "4AE32405EA360C5C33F23A1D2A4EDC011A0F3A9A09182624084FF1FCCDFF0295",
    "src/ashare_agent/data_center.py": "0294A451837019C0FA6678C359FB919884A066D674662F62F86A34204B99E6BD",
    "src/ashare_agent/data_center_models.py": "153699C313892C4610779316E138061ECB2BE770B2008A827E2D785B41BAEC62",
    "src/ashare_agent/services/portfolio_service.py": "41F3037DB2E0157B049B7496F30C36748A434B4108A5CC64C41872E71A892280",
    "src/ashare_agent/services/portfolio_risk_engine.py": "ED6CC9125795946954D6909033254A5D0A5B2E77D7D277FA04B2ED5D984336D2",
    "src/ashare_agent/services/portfolio_risk_store.py": "BEA51A648203BF3AD3CA9081E2C23F6B7B2A72BF109E4BFD177B422C7CEAC30C",
    "src/ashare_agent/services/exit_engine.py": "8E0ECAA5A20EECBCDBCAB5259238F989D7710DED8EFFCA6792C2AB732B2F5264",
    "src/ashare_agent/services/valuation_service.py": "5331F1E806A4EDFF6DD46A38311ACCE6B791349ADEA300C197760D66D29153B4",
    "src/ashare_agent/paper_portfolio.py": "69FD3B0E81E12CAF0BB21A3014924A58CD536561BBC1C4604142FAE195286DCD",
    "src/ashare_agent/execution_rules.py": "A9A791C9919639923168B6AA7C75BF7D36A029154952005D05FC7E45B814BB04",
    "src/ashare_agent/broker.py": "3E8C2B1BD4E222A3CA2BB7D0EA0CC40E4C16A8EBC6E3C40EB2C3CD46E6487045",
    "src/ashare_agent/cross_sectional_backtest.py": "1378875FEBCC15FD95C3180771145BE15B0D888E15E054B73ADD89BC2A8330C2",
    "src/ashare_agent/quant_lab/factor_research/service.py": "D40DA401B2EAE468EF0A8D4D610384E6728CBFE69C7AB37A2486396D5CE810BE",
}


def _config(**changes) -> RobustnessConfig:
    """Use a compact but statistically sufficient synthetic robustness grid."""
    values = dict(
        portfolio_sizes=(3, 5, 8),
        single_stock_limits=(0.10, 0.15),
        total_exposures=(0.50, 0.60),
        rebalance_frequencies=("weekly", "monthly"),
        commission_rates=(0.0003, 0.0008),
        slippage_rates=(0.0005, 0.0020),
        sell_tax_rates=(0.0005, 0.0010),
        cost_multipliers=(1.0, 2.0, 3.0),
        delay_modes=("t1_open", "t1_close", "t2_close"),
        bootstrap_iterations=100,
        bootstrap_block_length=10,
        rolling_window_periods=60,
        rolling_step_periods=30,
        cscv_slices=4,
        minimum_overfit_observations=60,
    )
    values.update(changes)
    return RobustnessConfig(**values)


def _path(request: ScenarioRequest, *, days: int = 180, regimes: bool = True) -> StrategyPath:
    """Generate one deterministic gross path whose perturbation follows the request identity."""
    dates = tuple(day.date().isoformat() for day in pd.bdate_range("2025-01-02", periods=days))
    index = np.arange(days, dtype=float)
    base = 0.00045 + np.sin(index / 8.0) * 0.0025 + np.cos(index / 17.0) * 0.001
    turnover = np.full(days, 0.08)
    if request.family == "parameter":
        key, value = next(iter(request.overrides.items()))
        signature = (sum(ord(char) for char in f"{key}:{value}") % 9) - 4
        base = base * (1.0 + signature * 0.025) + np.sin(index / (9 + abs(signature))) * 0.00015
        turnover = turnover * (1.0 + abs(signature) * 0.04)
    elif request.family == "delay":
        mode = str(request.overrides["execution_delay"])
        impact = {"t1_open": 0.00002, "t1_close": 0.00008, "t2_close": 0.00016}[mode]
        base = base - impact
    labels = tuple(("bull", "bear", "sideways")[(position // 30) % 3] for position in range(days))
    result_hash = sha256_json({
        "scenario_id": request.scenario_id,
        "returns": [round(value, 12) for value in base],
    })
    return StrategyPath(
        scenario_id=request.scenario_id,
        dates=dates,
        gross_returns=tuple(base),
        turnovers=tuple(turnover),
        market_regimes=labels if regimes else tuple(None for _ in dates),
        market_regime_source="synthetic_point_in_time_fixture" if regimes else "unavailable",
        engine_version="synthetic-shared-backtest-v1",
        result_hash=result_hash,
    )


def _paths(config: RobustnessConfig) -> dict[str, StrategyPath]:
    """Materialize the complete declared scenario set for unit analysis."""
    return {request.scenario_id: _path(request) for request in config.scenario_requests()}


def _spec(experiment_id: str = "PANGU-QL-ROBUSTNESS-SYNTHETIC-001") -> ExperimentSpec:
    """Create an immutable synthetic-only experiment specification."""
    return ExperimentSpec(
        experiment_id=experiment_id,
        name="策略稳健性合成合同测试",
        data_center_run_ids=("synthetic-dc-run-001",),
        dataset_version="synthetic-robustness-dv1",
        data_start="2025-01-02",
        data_end="2025-09-10",
        strategy_version="cross-sectional-v2.0.0",
        factor_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract-synthetic",
        code_hash="robustness-code-synthetic",
        code_commit=None,
        benchmark_id="cash",
        benchmark_version="cash-v1",
        cost_model_version="robustness-gross-repricing-v1",
        split=SplitDefinition(
            "final_holdout",
            DateRange("2022-01-01", "2022-12-31"),
            DateRange("2023-01-01", "2023-12-31"),
            DateRange("2024-01-01", "2024-12-31"),
            purge_days=20,
            embargo_days=20,
            final_holdout=DateRange("2025-01-01", "2025-12-31"),
        ),
        random_seed=42,
        config={"research_type": "strategy_robustness", "selection_policy": "none"},
        dataset_label="SYNTHETIC_TEST_ONLY",
    )


class _PassValidator:
    """Stand in for the already-tested Data Center gate during synthetic service tests."""

    def validate(self, spec: ExperimentSpec) -> DatasetValidation:
        return DatasetValidation(
            status=ValidationStatus.PASS,
            checked_at="2026-08-10T00:00:00+00:00",
            events=(ValidationEvent(
                code="SYNTHETIC_GATE", status=ValidationStatus.PASS,
                message="合成Data Center门禁仅用于工程测试", run_id=spec.data_center_run_ids[0],
            ),),
            dataset_version=spec.dataset_version,
            run_ids=spec.data_center_run_ids,
        )


def test_config_declares_ofat_scenarios_and_rejects_invalid_cscv():
    config = _config()
    requests = config.scenario_requests()
    assert requests[0].scenario_id == "baseline"
    assert all(len(item.overrides) <= 1 for item in requests)
    assert config.config_hash == replace(config).config_hash
    with pytest.raises(ContractError):
        _config(cscv_slices=5)


def test_strategy_path_contract_rejects_misalignment_and_non_shared_pipeline():
    request = ScenarioRequest("baseline", "baseline", {})
    valid = _path(request)
    assert valid.path_hash == _path(request).path_hash
    with pytest.raises(ContractError):
        replace(valid, gross_returns=valid.gross_returns[:-1])
    with pytest.raises(ContractError):
        replace(valid, used_shared_research_pipeline=False)


def test_parameter_cost_and_delay_stresses_are_descriptive_and_select_nothing():
    config = _config()
    paths = _paths(config)
    parameter = analyze_parameter_sensitivity(paths, config)
    cost = analyze_cost_stress(paths["baseline"], config)
    delay = analyze_delay_stress(paths, config)
    assert parameter["scenario_count"] == 9
    assert parameter["selected_best_scenario"] is None
    assert 0 <= parameter["stability_score"] <= 100
    assert cost["scenario_count"] == 10
    assert cost["auto_changes_cost_model"] is False
    assert min(item["annualized_return_impact"] for item in cost["scenarios"]) <= 0
    assert delay["best_delay_selected"] is None and delay["scenario_count"] == 3


def test_regime_requires_explicit_labels_and_never_infers_future_state():
    config = _config()
    available = analyze_regimes(_path(ScenarioRequest("baseline", "baseline", {})), config)
    unavailable = analyze_regimes(
        _path(ScenarioRequest("baseline", "baseline", {}), regimes=False), config
    )
    assert available["availability"] == "AVAILABLE"
    assert set(available["regimes"]) == {"bull", "bear", "sideways"}
    assert available["inferred_from_future_returns"] is False
    assert unavailable["availability"] == "UNAVAILABLE"


def test_bootstrap_and_rolling_are_seeded_and_report_distribution_not_best_path():
    config = _config()
    path = _path(ScenarioRequest("baseline", "baseline", {}))
    first = analyze_bootstrap(path, config)
    second = analyze_bootstrap(path, config)
    rolling = analyze_rolling_windows(path, config)
    assert first == second
    assert first["iterations"] == 100 and first["selected_simulation"] is None
    assert first["annualized_return"]["p05"] <= first["annualized_return"]["p95"]
    assert rolling["availability"] == "AVAILABLE" and rolling["window_count"] >= 4


def test_overfitting_dsr_pbo_and_underpowered_unavailable_semantics():
    config = _config()
    available = analyze_overfitting(_paths(config), config)
    assert available["trial_count"] == 10
    assert available["deflated_sharpe_ratio"]["availability"] == "AVAILABLE"
    assert available["pbo"]["availability"] == "AVAILABLE"
    assert 0 <= available["pbo"]["probability"] <= 1
    underpowered_config = _config(minimum_overfit_observations=500)
    unavailable = analyze_overfitting(_paths(underpowered_config), underpowered_config)
    assert unavailable["availability"] == "UNAVAILABLE"
    assert unavailable["pbo"]["availability"] == "UNAVAILABLE"


def test_robustness_score_refuses_missing_evidence():
    config = _config()
    paths = _paths(config)
    parameter = analyze_parameter_sensitivity(paths, config)
    cost = analyze_cost_stress(paths["baseline"], config)
    delay = analyze_delay_stress(paths, config)
    regime = analyze_regimes(paths["baseline"], config)
    bootstrap = analyze_bootstrap(paths["baseline"], config)
    rolling = analyze_rolling_windows(paths["baseline"], config)
    overfit = analyze_overfitting(paths, config)
    score = calculate_robustness_score(
        parameter=parameter, cost=cost, regime=regime, bootstrap=bootstrap,
        rolling=rolling, overfitting=overfit, config=config, out_of_sample_evidence=True,
    )
    assert score["availability"] == "AVAILABLE" and 0 <= score["score"] <= 100
    assert score["strategy_admission_decision"] == "NOT_EVALUATED"
    non_holdout = calculate_robustness_score(
        parameter=parameter, cost=cost, regime=regime, bootstrap=bootstrap,
        rolling=rolling, overfitting=overfit, config=config,
    )
    assert non_holdout["availability"] == "UNAVAILABLE"
    assert "out_of_sample_stability" in non_holdout["missing_components"]
    missing = calculate_robustness_score(
        parameter=parameter, cost=cost, regime={"adaptability_score": None},
        bootstrap=bootstrap, rolling=rolling, overfitting=overfit, config=config,
        out_of_sample_evidence=True,
    )
    assert missing["availability"] == "UNAVAILABLE" and missing["score"] is None


@pytest.fixture()
def robustness_run(tmp_path):
    """Run one sealed synthetic experiment with a fake pass at the Data Center boundary."""
    service = RobustnessService(tmp_path)
    service.validator = _PassValidator()
    config = _config()
    result = service.run(_spec(), scenario_runner=_path, config=config)
    return service, result


def test_service_registers_three_tables_seals_artifacts_and_has_zero_capabilities(robustness_run):
    service, result = robustness_run
    assert result.investment_validity == "NOT_ESTABLISHED"
    assert result.can_trade is False and result.can_create_orders is False
    assert len(result.report["sections"]) == 12
    assert result.report["ai_research_agent_interface"]["status"] == "reserved_not_implemented"
    assert result.report["strategy_admission_decision"] == "NOT_EVALUATED"
    names = {item["name"] for item in result.artifact_manifest["artifacts"]}
    assert {
        "parameter_sensitivity.json", "cost_stress.json", "delay_stress.json",
        "regime_test.json", "bootstrap.json", "rolling_analysis.json", "overfitting.json",
        "robustness_score.json", "robustness_report.json", "strategy_paths.json",
    }.issubset(names)
    assert (service.artifacts.run_dir(result.experiment_id, result.run_id) / ".completed").exists()
    registered = service.registry.robustness_report(result.run_id)
    assert registered["run_id"] == result.run_id
    with sqlite3.connect(service.registry.path) as connection:
        counts = tuple(connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (result.run_id,)
        ).fetchone()[0] for table in (
            "robustness_experiments", "robustness_metrics", "robustness_reports"
        ))
        flags = connection.execute(
            """SELECT can_trade,can_create_orders,can_modify_strategy,can_promote_strategy
               FROM robustness_experiments WHERE run_id=?""", (result.run_id,)
        ).fetchone()
    assert counts[0] == 1 and counts[1] > 20 and counts[2] == 1
    assert flags == (0, 0, 0, 0)


def test_registry_database_rejects_any_robustness_execution_capability(robustness_run):
    service, result = robustness_run
    with sqlite3.connect(service.registry.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE robustness_experiments SET can_promote_strategy=1 WHERE run_id=?",
                (result.run_id,),
            )


def test_same_spec_replay_uses_new_run_and_never_overwrites(tmp_path):
    service = RobustnessService(tmp_path)
    service.validator = _PassValidator()
    spec = _spec("PANGU-QL-ROBUSTNESS-SYNTHETIC-REPLAY")
    first = service.run(spec, scenario_runner=_path, config=_config())
    second = service.run(spec, scenario_runner=_path, config=_config())
    assert first.run_id != second.run_id
    assert service.artifacts.run_dir(spec.experiment_id, first.run_id).exists()
    assert service.artifacts.run_dir(spec.experiment_id, second.run_id).exists()


def test_report_contains_synthetic_warning_no_secret_and_no_validity_claim(robustness_run):
    service, result = robustness_run
    path = service.artifacts.run_dir(result.experiment_id, result.run_id) / "robustness_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in serialized and "password" not in serialized
    assert "不证明策略稳健" in payload["synthetic_warning"]
    assert payload["investment_validity"] == "NOT_ESTABLISHED"
    assert payload["can_promote_strategy"] is False


def test_protected_hashes_and_robustness_import_boundary_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in PROTECTED_HASHES.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest().upper() == expected
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/ashare_agent/quant_lab/robustness").glob("*.py")
    ).lower()
    forbidden = (
        "paper_portfolio", "place_order", "submit_order", "execute_order",
        "live_trading_enabled", "production_factor_weights =",
    )
    assert all(term not in source for term in forbidden)
