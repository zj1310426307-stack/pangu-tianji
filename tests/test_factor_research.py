from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from ashare_agent.data_center import DataCenter
from ashare_agent.research_pipeline import ResearchPipelineResult
from ashare_agent.quant_lab.contracts import (
    DateRange,
    ExperimentSpec,
    SplitDefinition,
    aggregate_dataset_version,
)
from ashare_agent.quant_lab.exceptions import ContractError
from ashare_agent.quant_lab.experiment_registry import ExperimentRegistry
from ashare_agent.quant_lab.factor_research.ablation import analyze_ablation
from ashare_agent.quant_lab.factor_research.ablation_engine import AblationEngine
from ashare_agent.quant_lab.factor_research.contracts import (
    FACTOR_COLUMNS,
    FACTOR_NAMES,
    FactorResearchConfig,
)
from ashare_agent.quant_lab.factor_research.decay_analysis import analyze_decay
from ashare_agent.quant_lab.factor_research.correlation_engine import CorrelationEngine
from ashare_agent.quant_lab.factor_research.decay_engine import DecayEngine
from ashare_agent.quant_lab.factor_research.factor_correlation import analyze_factor_correlation
from ashare_agent.quant_lab.factor_research.factor_return import (
    FactorPanelLoader,
    analyze_factor_returns,
)
from ashare_agent.quant_lab.factor_research.ic_analysis import analyze_rank_ic
from ashare_agent.quant_lab.factor_research.ic_engine import RankICEngine
from ashare_agent.quant_lab.factor_research.quantile_engine import QuantileEngine
from ashare_agent.quant_lab.factor_research.quantile_analysis import analyze_quantiles
from ashare_agent.quant_lab.factor_research.service import FactorResearchService
from ashare_agent.quant_lab.factor_research.regime_engine import RegimeEngine
from ashare_agent.quant_lab.factor_research.stability import analyze_stability


DATASET_LABEL = "SYNTHETIC_TEST_ONLY"
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
}


def _panel(days: int = 135, symbols: int = 20) -> pd.DataFrame:
    """Create a deterministic synthetic panel for mathematical contract tests only."""
    dates = pd.bdate_range("2025-01-02", periods=days)
    rows = []
    for day_index, research_date in enumerate(dates):
        regime = ("bull", "bear", "sideways")[(day_index // 20) % 3]
        for symbol_index in range(symbols):
            centered = (symbol_index - (symbols - 1) / 2) / symbols
            row = {
                "date": research_date,
                "symbol": f"{600000 + symbol_index:06d}.SH",
                "market_regime": regime,
                "source_run_id": f"synthetic-run-{day_index:03d}",
                "close": 10.0 + symbol_index * 0.1 + day_index * 0.01,
            }
            for factor_index, factor_name in enumerate(FACTOR_NAMES):
                row[FACTOR_COLUMNS[factor_name]] = (
                    centered * (1.0 - factor_index * 0.04)
                    + np.sin((day_index + factor_index) / 7.0) * 0.05
                )
            for horizon in (1, 5, 10, 20, 60, 120):
                row[f"forward_return_{horizon}d"] = (
                    centered * 0.02 * np.sqrt(horizon)
                    + np.cos((day_index + symbol_index) / 11.0) * 0.002
                ) if day_index + horizon < days else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def _research_result(day: pd.Timestamp, day_index: int, symbols: int = 10) -> ResearchPipelineResult:
    """Build one complete synthetic Data Center research snapshot."""
    symbol_values = [f"{600000 + index:06d}.SH" for index in range(symbols)]
    universe = pd.DataFrame([
        {
            "symbol": symbol, "name": f"合成{index}", "industry": f"行业{index % 3}",
            "list_date": "2000-01-01", "delist_date": None,
            "corporate_action_version": "synthetic-v1",
        }
        for index, symbol in enumerate(symbol_values)
    ])
    prices = [10.0 + index * 0.2 + day_index * 0.05 for index in range(symbols)]
    snapshot = pd.DataFrame([
        {
            "symbol": symbol, "last_price": price, "turnover": 100_000_000 + index * 1_000_000,
            "volume": 1_000_000 + index * 1_000, "suspended": False,
            "upper_limit_price": price * 1.1, "lower_limit_price": price * 0.9,
        }
        for index, (symbol, price) in enumerate(zip(symbol_values, prices))
    ])
    history = pd.DataFrame([
        {
            "date": day.date().isoformat(), "symbol": symbol, "close": price,
            "turnover": 100_000_000 + index * 1_000_000,
        }
        for index, (symbol, price) in enumerate(zip(symbol_values, prices))
    ])
    fundamentals = pd.DataFrame([
        {
            "symbol": symbol,
            "available_at": (day.to_pydatetime() - timedelta(days=30)).date().isoformat(),
            "roe": 0.1 + index * 0.001,
        }
        for index, symbol in enumerate(symbol_values)
    ])
    features = snapshot.assign(
        momentum=np.linspace(-0.1, 0.2, symbols),
        volatility=np.linspace(0.3, 0.1, symbols),
        max_drawdown=np.linspace(-0.2, -0.05, symbols),
    )
    factors = features.copy()
    for factor_index, factor_name in enumerate(FACTOR_NAMES):
        factors[FACTOR_COLUMNS[factor_name]] = (
            np.arange(symbols, dtype=float) * (1.0 + factor_index / 10.0)
            + np.sin(day_index / 3.0 + factor_index)
        )
    factors["score"] = factors[list(FACTOR_COLUMNS.values())].sum(axis=1)
    factors["market_regime"] = ("bull", "bear", "sideways")[(day_index // 4) % 3]
    ranked = factors.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    return ResearchPipelineResult(
        strategy_version="cross-sectional-v2.0.0",
        factor_model_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract-synthetic",
        as_of=day,
        raw_universe=universe,
        raw_snapshot=snapshot,
        raw_history=history,
        raw_fundamentals=fundamentals,
        universe=universe,
        snapshot=snapshot,
        features=features,
        factor_results=factors,
        ranked=ranked,
        targets=list(ranked.head(3)["symbol"]),
        target_weights={symbol: 0.1 for symbol in ranked.head(3)["symbol"]},
        portfolio_rejections={},
        return_history={},
        stage_counts={
            "security_universe": symbols, "data_snapshot": symbols,
            "feature_calculation": symbols, "factor_score": symbols,
            "ranking": symbols, "portfolio_construction": 3,
        },
        data_quality={"financial_coverage": 1.0},
    )


@pytest.fixture(scope="module")
def factor_center(tmp_path_factory):
    """Persist twelve small synthetic daily runs once for integration tests."""
    root = tmp_path_factory.mktemp("factor_research")
    center = DataCenter(root / "output" / "data_center")
    records = []
    dates = pd.bdate_range("2026-01-05", periods=12)
    for day_index, day in enumerate(dates):
        regime = ("bull", "bear", "sideways")[(day_index // 4) % 3]
        record = center.record_research(
            _research_result(day, day_index),
            run_kind="synthetic_factor_test",
            metadata={"provider": "synthetic_test", "market_regime": regime},
        )
        with sqlite3.connect(center.catalog_path) as connection:
            connection.execute(
                "UPDATE research_runs SET verified=1 WHERE run_id=?", (record.run_id,)
            )
        records.append(record)
    return root, center, records


def _spec(records, **changes) -> ExperimentSpec:
    values = dict(
        experiment_id="PANGU-QL-FACTOR-SYNTHETIC-001",
        name="七因子合成合同测试",
        data_center_run_ids=tuple(record.run_id for record in records),
        dataset_version=aggregate_dataset_version([record.data_version for record in records]),
        data_start=records[0].research_date,
        data_end=records[-1].research_date,
        strategy_version="cross-sectional-v2.0.0",
        factor_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract-synthetic",
        code_hash="factor-research-code-synthetic",
        code_commit=None,
        benchmark_id="cash",
        benchmark_version="cash-v1",
        cost_model_version="not_applicable_factor_forward_return-v1",
        split=SplitDefinition(
            "final_holdout",
            DateRange("2024-01-01", "2024-06-30"),
            DateRange("2024-07-01", "2024-09-30"),
            DateRange("2024-10-01", "2024-12-31"),
            purge_days=5,
            embargo_days=5,
            final_holdout=DateRange("2025-01-01", "2025-03-31"),
        ),
        random_seed=42,
        config={"research_type": "factor_research", "weight_policy": "frozen_no_auto_adjust"},
        dataset_label=DATASET_LABEL,
    )
    values.update(changes)
    return ExperimentSpec(**values)


@pytest.fixture(scope="module")
def factor_run(factor_center):
    """Run one compact end-to-end synthetic experiment for shared assertions."""
    root, center, records = factor_center
    service = FactorResearchService(root, center.root)
    config = FactorResearchConfig(
        horizons=(1, 5), quantile_counts=(5,), ablation_horizon=5
    )
    return service, service.run(_spec(records), config=config)


def test_config_contract_and_hash_are_deterministic():
    config = FactorResearchConfig()
    assert config.horizons == (1, 5, 10, 20, 60, 120)
    assert config.config_hash == replace(config).config_hash
    with pytest.raises(ContractError):
        FactorResearchConfig(quantile_counts=(4,))


def test_registry_additive_migration_preserves_older_factor_metrics_schema(tmp_path):
    path = tmp_path / "experiments.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE factor_metrics(
                run_id TEXT NOT NULL, factor_name TEXT NOT NULL, horizon INTEGER NOT NULL,
                period TEXT NOT NULL, ic_mean REAL, ic_std REAL, icir REAL,
                ic_hit_rate REAL, t_stat REAL, p_value REAL,
                observation_count INTEGER NOT NULL, created_at TEXT NOT NULL,
                can_trade INTEGER NOT NULL DEFAULT 0, can_create_orders INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(run_id,factor_name,horizon)
            )"""
        )
    ExperimentRegistry(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(factor_metrics)")}
    assert {
        "annual_return", "annual_volatility", "max_drawdown", "turnover",
        "long_short_return", "sharpe", "redundancy_score",
    }.issubset(columns)


def test_rank_ic_all_factors_horizons_and_significance():
    result = analyze_rank_ic(_panel(), horizons=(1, 5, 10, 20, 60, 120), minimum_cross_section=5)
    assert set(result["factors"]) == set(FACTOR_NAMES)
    assert set(result["factors"]["quality"]) == {"1", "5", "10", "20", "60", "120"}
    assert result["factors"]["quality"]["20"]["observation_count"] > 0
    p_value = result["factors"]["quality"]["20"]["p_value"]
    assert p_value is None or 0 <= p_value <= 1


def test_quantile_five_ten_group_outputs_return_risk_turnover_and_spread():
    result = analyze_quantiles(
        _panel(), horizons=(5,), quantile_counts=(5, 10), minimum_cross_section=5,
        periods_per_year=252,
    )
    quintile = result["factors"]["value"]["5"]["5"]
    decile = result["factors"]["value"]["10"]["5"]
    assert len(quintile["groups"]) == 5 and len(decile["groups"]) == 10
    assert {"annualized_return", "annualized_volatility", "max_drawdown", "turnover"}.issubset(
        quintile["groups"]["Q5"]
    )
    assert quintile["long_short"]["definition"] == "Q5-Q1"


def test_rank_weighted_factor_return_is_research_only_and_complete():
    result = analyze_factor_returns(_panel(), horizons=(1, 20), periods_per_year=252)
    assert set(result["factors"]) == set(FACTOR_NAMES)
    assert result["research_only"] is True
    metrics = result["factors"]["momentum"]["20"]
    assert metrics["portfolio_definition"] == "demeaned_percentile_rank_dollar_neutral"
    assert {"mean_forward_return", "annualized_volatility", "max_drawdown", "turnover"}.issubset(metrics)


def test_decay_correlation_and_ablation_are_descriptive_only():
    panel = _panel()
    ic = analyze_rank_ic(panel, horizons=(1, 5, 20), minimum_cross_section=5)
    quantiles = analyze_quantiles(
        panel, horizons=(1, 5, 20), quantile_counts=(5,), minimum_cross_section=5,
        periods_per_year=252,
    )
    decay = analyze_decay(ic, quantiles)
    correlation = analyze_factor_correlation(
        panel, minimum_cross_section=5, high_correlation_threshold=0.8
    )
    ablation = analyze_ablation(
        panel, horizon=20, top_fraction=0.2, periods_per_year=252
    )
    assert set(decay["factors"]) == set(FACTOR_NAMES)
    matrix = np.asarray(correlation["matrix"])
    assert matrix.shape == (7, 7) and np.allclose(matrix, matrix.T)
    assert np.allclose(np.diag(matrix), 1.0)
    assert set(correlation["redundancy_scores"]) == set(FACTOR_NAMES)
    assert all(0 <= value <= 1 for value in correlation["redundancy_scores"].values())
    assert correlation["automatic_factor_merge"] is False
    assert len(ablation["models"]) == 8
    assert "annualized_sharpe" in ablation["models"]["full_seven_factor"]
    assert "sharpe_change_ablated_minus_full" in ablation["contribution_order"][0]
    assert ablation["changes_production_weights"] is False
    assert ablation["auto_applies_results"] is False


def test_named_engines_reuse_the_single_analysis_contract():
    panel = _panel(days=30, symbols=10)
    config = FactorResearchConfig(
        horizons=(1, 5), quantile_counts=(5, 10), ablation_horizon=5
    )
    ic = RankICEngine().run(panel, config)
    quantile_bundle = QuantileEngine().run(panel, config)
    decay = DecayEngine().run(ic, quantile_bundle["quantiles"])
    correlation = CorrelationEngine().run(panel, config)
    ablation = AblationEngine().run(panel, config)
    regime = RegimeEngine().run(panel, config)
    assert set(ic["factors"]) == set(FACTOR_NAMES)
    assert set(decay["factors"]) == set(FACTOR_NAMES)
    assert correlation["redundancy_scores"]
    assert len(ablation["models"]) == 8
    assert regime["availability"] == "available"


def test_market_regime_stability_requires_explicit_point_in_time_labels():
    panel = _panel()
    available = analyze_stability(
        panel, horizons=(5,), minimum_cross_section=5, periods_per_year=252
    )
    unavailable = analyze_stability(
        panel.assign(market_regime=None), horizons=(5,), minimum_cross_section=5,
        periods_per_year=252,
    )
    assert available["availability"] == "available"
    assert set(available["regimes"]) == {"bull", "bear", "sideways"}
    assert available["inferred_from_future_returns"] is False
    assert unavailable["availability"] == "unavailable"


def test_data_center_loader_binds_run_ids_and_uses_future_price_shift(factor_center):
    _, center, records = factor_center
    loaded = FactorPanelLoader(center.root).load(
        [record.run_id for record in records],
        horizons=(1, 5),
        data_start=records[0].research_date,
        data_end=records[-1].research_date,
    )
    assert loaded.manifest["run_count"] == len(records)
    assert loaded.manifest["source"] == "Pangu Data Center"
    symbol = loaded.frame["symbol"].iloc[0]
    rows = loaded.frame[loaded.frame["symbol"] == symbol].sort_values("date")
    expected = rows["close"].iloc[1] / rows["close"].iloc[0] - 1
    assert rows["forward_return_1d"].iloc[0] == pytest.approx(expected)
    assert pd.isna(rows["forward_return_5d"].iloc[-1])


def test_factor_research_service_registers_tables_seals_artifacts_and_never_trades(factor_run):
    service, result = factor_run
    assert result.dataset_label == DATASET_LABEL
    assert result.can_trade is False and result.can_create_orders is False
    assert result.factor_report["experiment_id"] == result.experiment_id
    assert result.factor_report["run_id"] == result.run_id
    assert result.factor_report["investment_validity"] == "NOT_ESTABLISHED"
    assert result.metrics["stability_availability"] == "available"
    assert len(result.factor_report["sections"]) == 10
    assert result.factor_report["ai_research_analyst_interface"]["status"] == "reserved_not_implemented"
    assert result.factor_report["ai_research_analyst_interface"]["can_modify_weights"] is False
    assert "不证明因子有效" in result.factor_report["synthetic_warning"]
    names = {item["name"] for item in result.artifact_manifest["artifacts"]}
    assert {
        "ic_analysis.json", "factor_return.json", "quantile_analysis.json", "decay_analysis.json",
        "factor_correlation.json", "ablation.json", "stability.json",
        "factor_report.json", "metrics.json", "factor_dataset_manifest.json",
    }.issubset(names)
    ic_artifact = json.loads(
        (service.artifacts.run_dir(result.experiment_id, result.run_id) / "ic_analysis.json")
        .read_text(encoding="utf-8")
    )
    assert ic_artifact["experiment_id"] == result.experiment_id
    assert ic_artifact["run_id"] == result.run_id
    assert ic_artifact["data_version"] == result.dataset_version
    assert (service.artifacts.run_dir(result.experiment_id, result.run_id) / ".completed").exists()
    registered = service.registry.factor_report(result.run_id)
    assert registered["run_id"] == result.run_id
    with sqlite3.connect(service.registry.path) as connection:
        factor_count = connection.execute(
            "SELECT COUNT(*) FROM factor_metrics WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
        flags = connection.execute(
            "SELECT can_trade,can_create_orders FROM factor_experiments WHERE run_id=?",
            (result.run_id,),
        ).fetchone()
        rich_metric = connection.execute(
            """SELECT annual_return,annual_volatility,max_drawdown,turnover,
                long_short_return,sharpe,redundancy_score
                FROM factor_metrics WHERE run_id=? AND factor_name='value' AND horizon=1""",
            (result.run_id,),
        ).fetchone()
    assert factor_count == 7 * 2
    assert flags == (0, 0)
    assert len(rich_metric) == 7
    assert all(rich_metric[index] is not None for index in (0, 1, 2, 3, 4, 6))
    assert result.metrics["engine_versions"]["report_generator"] == "factor-report-generator-v1.0.0"


def test_same_experiment_replay_gets_new_run_without_overwrite(factor_center):
    root, center, records = factor_center
    service = FactorResearchService(root, center.root)
    spec = _spec(records, experiment_id="PANGU-QL-FACTOR-SYNTHETIC-REPLAY")
    config = FactorResearchConfig(
        horizons=(1, 5), quantile_counts=(5,), ablation_horizon=5
    )
    first = service.run(spec, config=config)
    second = service.run(spec, config=config)
    assert first.run_id != second.run_id
    assert service.artifacts.run_dir(spec.experiment_id, first.run_id).exists()
    assert service.artifacts.run_dir(spec.experiment_id, second.run_id).exists()


def test_protected_hashes_and_factor_research_import_boundary_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in PROTECTED_HASHES.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest().upper() == expected
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/ashare_agent/quant_lab/factor_research").glob("*.py")
    ).lower()
    forbidden = (
        "paper_portfolio", "broker", "place_order", "submit_order", "execute_order",
        "live_trading_enabled", "production_factor_weights =",
    )
    assert all(term not in source for term in forbidden)


def test_report_artifact_has_no_secret_or_investment_validity_claim(factor_run):
    service, result = factor_run
    path = service.artifacts.run_dir(result.experiment_id, result.run_id) / "factor_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in serialized and "password" not in serialized
    assert payload["investment_validity"] == "NOT_ESTABLISHED"
    assert payload["production_weights_changed"] is False
    assert payload["auto_weight_adjustment"] is False
