from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from ashare_agent.data_center import DataCenter
from ashare_agent.research_pipeline import ResearchPipelineResult
from ashare_agent.quant_lab.artifacts import ArtifactStore
from ashare_agent.quant_lab.benchmark import BenchmarkContract, BenchmarkProvider
from ashare_agent.quant_lab.contracts import (
    DateRange,
    ExperimentResult,
    ExperimentSpec,
    ExperimentState,
    SplitDefinition,
    StrategyLifecycleState,
    ValidationStatus,
)
from ashare_agent.quant_lab.dataset_validator import DatasetValidator
from ashare_agent.quant_lab.exceptions import (
    ArtifactIntegrityError,
    ContractError,
    ImmutableExperimentError,
)
from ashare_agent.quant_lab.experiment_registry import ExperimentRegistry
from ashare_agent.quant_lab.metrics import calculate_metrics
from ashare_agent.quant_lab.service import QuantLabService
from ashare_agent.quant_lab.splitters import (
    ExpandingWalkForward,
    FinalHoldout,
    RollingWalkForward,
)


PROTECTED_HASHES = {
    "src/ashare_agent/research_pipeline.py": "1FD79ED464E75EE1D6F1477AB7267178FD00D9329AA8EFC311C5B57058678577",
    "src/ashare_agent/factor_model.py": "4AE32405EA360C5C33F23A1D2A4EDC011A0F3A9A09182624084FF1FCCDFF0295",
    "src/ashare_agent/data_center.py": "0294A451837019C0FA6678C359FB919884A066D674662F62F86A34204B99E6BD",
    "src/ashare_agent/data_center_models.py": "153699C313892C4610779316E138061ECB2BE770B2008A827E2D785B41BAEC62",
    "src/ashare_agent/services/portfolio_service.py": "41F3037DB2E0157B049B7496F30C36748A434B4108A5CC64C41872E71A892280",
    "src/ashare_agent/services/portfolio_risk_engine.py": "ED6CC9125795946954D6909033254A5D0A5B2E77D7D277FA04B2ED5D984336D2",
    "src/ashare_agent/services/portfolio_risk_store.py": "BEA51A648203BF3AD3CA9081E2C23F6B7B2A72BF109E4BFD177B422C7CEAC30C",
    "src/ashare_agent/paper_portfolio.py": "940A25CFF6FE871531D0272B6C17592619ED434D597190D478F7C2C3D94D28F0",
}


def _result() -> ResearchPipelineResult:
    universe = pd.DataFrame([
        {"symbol": "600000.SH", "name": "甲公司", "industry": "银行",
         "list_date": "2000-01-01", "delist_date": None},
        {"symbol": "600001.SH", "name": "乙公司", "industry": "制造",
         "list_date": "2001-01-01", "delist_date": None},
    ])
    snapshot = pd.DataFrame([
        {"symbol": "600000.SH", "last_price": 10.0, "turnover": 100_000_000,
         "volume": 1_000_000, "suspended": False,
         "upper_limit_price": 11.0, "lower_limit_price": 9.0},
        {"symbol": "600001.SH", "last_price": 12.0, "turnover": 90_000_000,
         "volume": 900_000, "suspended": False,
         "upper_limit_price": 13.2, "lower_limit_price": 10.8},
    ])
    history = pd.DataFrame([
        {"symbol": symbol, "date": day, "close": price, "turnover": turnover}
        for symbol, price, turnover in (
            ("600000.SH", 10.0, 100_000_000), ("600001.SH", 12.0, 90_000_000)
        )
        for day in ("2026-07-19", "2026-07-20")
    ])
    fundamentals = pd.DataFrame([
        {"symbol": "600000.SH", "available_at": "2026-06-30", "roe": 0.12},
        {"symbol": "600001.SH", "available_at": "2026-06-30", "roe": 0.10},
    ])
    features = snapshot.assign(momentum=[0.1, 0.08], volatility=[0.2, 0.25],
                               max_drawdown=[-0.08, -0.10])
    factors = features.assign(score=[82.0, 77.0])
    ranked = factors.assign(rank=[1, 2])
    return ResearchPipelineResult(
        strategy_version="cross-sectional-v2.0.0",
        factor_model_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract-test",
        as_of=pd.Timestamp("2026-07-20"),
        raw_universe=universe,
        raw_snapshot=snapshot,
        raw_history=history,
        raw_fundamentals=fundamentals,
        universe=universe,
        snapshot=snapshot,
        features=features,
        factor_results=factors,
        ranked=ranked,
        targets=["600000.SH"],
        target_weights={"600000.SH": 0.2},
        portfolio_rejections={"600001.SH": "测试容量"},
        return_history={},
        stage_counts={"security_universe": 2, "data_snapshot": 2,
                      "feature_calculation": 2, "factor_score": 2,
                      "ranking": 2, "portfolio_construction": 1},
        data_quality={"financial_coverage": 1.0},
    )


def _center(tmp_path: Path, *, verified: bool = True):
    center = DataCenter(tmp_path / "output" / "data_center")
    record = center.record_research(_result(), run_kind="historical_backtest",
                                    metadata={"provider": "synthetic_test"})
    if verified:
        with sqlite3.connect(center.catalog_path) as connection:
            connection.execute("UPDATE research_runs SET verified=1 WHERE run_id=?", (record.run_id,))
    return center, record


def _spec(record, **changes) -> ExperimentSpec:
    values = dict(
        experiment_id="PANGU-QL-TEST-001",
        name="合成测试实验",
        data_center_run_ids=(record.run_id,),
        dataset_version=record.data_version,
        data_start="2026-07-01",
        data_end="2026-07-31",
        strategy_version="cross-sectional-v2.0.0",
        factor_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract-test",
        code_hash="code-hash-test",
        code_commit=None,
        benchmark_id="cash",
        benchmark_version="cash-v1",
        cost_model_version="a-share-cost-v1",
        split=SplitDefinition(
            "final_holdout",
            DateRange("2025-01-01", "2025-12-31"),
            DateRange("2026-01-01", "2026-03-31"),
            DateRange("2026-04-01", "2026-06-30"),
            purge_days=5,
            embargo_days=5,
            final_holdout=DateRange("2026-07-01", "2026-07-31"),
        ),
        random_seed=42,
        config={"cost_model": {"commission_rate": 0.0003, "minimum_commission": 5.0}},
        dataset_label="SYNTHETIC_TEST_ONLY",
    )
    values.update(changes)
    return ExperimentSpec(**values)


def test_spec_and_result_hash_are_deterministic(tmp_path):
    _, record = _center(tmp_path)
    assert _spec(record).spec_hash == _spec(record).spec_hash
    result = ExperimentResult(
        "PANGU-QL-TEST-001", "run-fixed", ExperimentState.COMPLETED,
        StrategyLifecycleState.DATA_VERIFIED, "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00",
        {"dataset": "x"}, {"folds": 1}, {"return": 0.1}, {"id": "cash"},
        {"manifest_hash": "abc"},
    )
    assert result.result_hash == replace(result).result_hash
    assert result.can_trade is False and result.can_create_orders is False


def test_registry_rerun_creates_new_run_and_completed_is_immutable(tmp_path):
    _, record = _center(tmp_path)
    registry = ExperimentRegistry(tmp_path / "experiments.sqlite3")
    registry.register(_spec(record))
    first = registry.create_run("PANGU-QL-TEST-001")
    second = registry.create_run("PANGU-QL-TEST-001")
    assert first != second
    registry.transition(first, ExperimentState.VALIDATING)
    registry.transition(first, ExperimentState.RUNNING)
    registry.transition(first, ExperimentState.COMPLETED, result_hash="hash")
    with pytest.raises(ImmutableExperimentError):
        registry.transition(first, ExperimentState.FAILED)


def test_dataset_validator_rejects_unverified_run(tmp_path):
    center, record = _center(tmp_path, verified=False)
    validation = DatasetValidator(center.root).validate(_spec(record))
    assert validation.status == ValidationStatus.BLOCKED
    assert "RUN_UNVERIFIED" in {item.code for item in validation.events}


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("strategy_version", "different", "STRATEGY_VERSION_MISMATCH"),
        ("factor_contract_hash", "different", "FACTOR_HASH_MISMATCH"),
        ("dataset_version", "different", "DATASET_VERSION_MISMATCH"),
    ],
)
def test_dataset_validator_rejects_version_drift(tmp_path, field, value, code):
    center, record = _center(tmp_path)
    validation = DatasetValidator(center.root).validate(_spec(record, **{field: value}))
    assert validation.status == ValidationStatus.BLOCKED
    assert code in {item.code for item in validation.events}


def test_dataset_validator_detects_tampered_asset(tmp_path):
    center, record = _center(tmp_path)
    manifest = center.manifest(record.run_id)
    asset = manifest["assets"]["feature_store"]
    path = center.root / asset["path"]
    path.write_bytes(gzip.compress(b"[]", mtime=0))
    validation = DatasetValidator(center.root).validate(_spec(record))
    assert "ASSET_HASH_MISMATCH" in {item.code for item in validation.events}


def test_dataset_validator_detects_future_available_at_even_with_rehashed_asset(tmp_path):
    center, record = _center(tmp_path)
    manifest_path = record.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = manifest["assets"]["financial_point_in_time"]
    path = center.root / asset["path"]
    rows = json.loads(gzip.decompress(path.read_bytes()))
    rows[0]["available_at"] = "2026-08-01"
    content = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    new_hash = hashlib.sha256(content).hexdigest()
    new_path = path.with_name(f"{new_hash}.json.gz")
    new_path.write_bytes(gzip.compress(content, mtime=0))
    asset.update({"path": new_path.relative_to(center.root).as_posix(), "sha256": new_hash})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    validation = DatasetValidator(center.root).validate(_spec(record))
    assert "FUTURE_AVAILABLE_AT" in {item.code for item in validation.events}


def test_walk_forward_purge_embargo_and_final_holdout_guard():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(160)]
    manifest = ExpandingWalkForward().split(
        days, minimum_train=40, validation_size=15, test_size=10,
        purge_days=3, embargo_days=3, final_holdout_size=20,
    )
    assert manifest.folds and manifest.final_holdout
    first = manifest.folds[0]
    assert date.fromisoformat(first.train.end) < date.fromisoformat(first.validation.start)
    assert date.fromisoformat(first.validation.end) < date.fromisoformat(first.test.start)
    guard = FinalHoldout(manifest.final_holdout)
    with pytest.raises(ContractError, match="final holdout"):
        guard.assert_selection_range("2025-01-01", manifest.final_holdout.start)


def test_rolling_walk_forward_and_overlap_rejection():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(120)]
    assert RollingWalkForward().split(
        days, train_size=30, validation_size=10, test_size=10,
        purge_days=2, embargo_days=2,
    ).folds
    with pytest.raises(ContractError, match="无重叠"):
        SplitDefinition(
            "final_holdout", DateRange("2025-01-01", "2025-02-01"),
            DateRange("2025-02-01", "2025-03-01"),
            DateRange("2025-04-01", "2025-05-01"),
        ).validate()


def test_real_index_benchmark_is_honestly_unavailable():
    contract = BenchmarkContract("000300.SH", "ths-index-v1", "real_index", "ths")
    result = BenchmarkProvider().real_index(contract, None)
    assert result.availability == "unavailable"
    assert result.returns == ()


def test_artifact_manifest_detects_tampering_and_seals_completed(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    item = store.write_json("exp-001", "run-001", "metrics.json", {"return": 0.1})
    manifest = store.finalize("exp-001", "run-001", [item])
    target = store.root / item["path"]
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        store.verify("exp-001", "run-001", manifest)
    with pytest.raises(ImmutableExperimentError):
        store.write_json("exp-001", "run-001", "new.json", {})


def test_metrics_include_cost_turnover_and_benchmark():
    curve = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5),
                          "equity": [100, 101, 100, 103, 104]})
    trades = pd.DataFrame([
        {"side": "BUY", "fill_price": 10, "quantity": 10, "fee": 1,
         "slippage_cost": 0.2, "realized_pnl": 0},
        {"side": "SELL", "fill_price": 11, "quantity": 10, "fee": 1,
         "slippage_cost": 0.2, "realized_pnl": 8},
    ])
    benchmark = pd.Series([0.0, 0.001, -0.001, 0.002], index=curve["date"].iloc[1:])
    metrics = calculate_metrics(curve, trades, benchmark)
    assert metrics["transaction_cost"] == 2
    assert metrics["slippage_cost"] == pytest.approx(0.4)
    assert "information_ratio" in metrics


def test_service_writes_required_artifacts_cost_hash_and_no_trade_capability(tmp_path):
    center, record = _center(tmp_path)
    service = QuantLabService(tmp_path, center.root)
    spec = _spec(record)
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(160)]
    split = ExpandingWalkForward().split(
        dates, minimum_train=40, validation_size=15, test_size=10,
        purge_days=3, embargo_days=3, final_holdout_size=20,
    )
    benchmark = BenchmarkProvider().cash(
        ["2026-07-20"], BenchmarkContract("cash", "cash-v1", "cash", "deterministic")
    )
    result = service.run_experiment(
        spec, split_manifest=split, benchmark=benchmark,
        backtest_runner=lambda: {
            "summary": {"total_return": 0.0, "dataset_label": "SYNTHETIC_TEST_ONLY"},
            "equity_records": [{"date": "2026-07-20", "equity": 100000}],
            "trade_records": [], "ranking_records": [],
        },
    )
    assert result.state == ExperimentState.COMPLETED
    assert result.can_trade is False and result.can_create_orders is False
    names = {item["name"] for item in result.artifact_manifest["artifacts"]}
    assert {
        "experiment_spec.json", "dataset_manifest.json", "split_manifest.json",
        "strategy_manifest.json", "benchmark_manifest.json", "cost_model.json",
        "validation_events.json", "metrics.json",
    }.issubset(names)
    cost_path = service.artifacts.run_dir(spec.experiment_id, result.run_id) / "cost_model.json"
    cost = json.loads(cost_path.read_text(encoding="utf-8"))
    assert len(cost["config_hash"]) == 64
    assert service.registry.run(result.run_id)["result_hash"] == result.result_hash


def test_failed_runner_is_audited_and_not_retried(tmp_path):
    center, record = _center(tmp_path)
    service = QuantLabService(tmp_path, center.root)
    spec = _spec(record, experiment_id="PANGU-QL-TEST-FAIL")
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(140)]
    split = ExpandingWalkForward().split(
        days, minimum_train=40, validation_size=10, test_size=10,
        purge_days=2, embargo_days=2,
    )
    benchmark = BenchmarkProvider().cash(
        ["2026-07-20"], BenchmarkContract("cash", "cash-v1", "cash", "deterministic")
    )
    with pytest.raises(RuntimeError, match="boom"):
        service.run_experiment(
            spec, split_manifest=split, benchmark=benchmark,
            backtest_runner=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    with sqlite3.connect(service.registry.path) as connection:
        row = connection.execute(
            "SELECT state,error_code FROM experiment_runs WHERE experiment_id=?",
            (spec.experiment_id,),
        ).fetchone()
    assert row == ("FAILED", "RuntimeError")


def test_protected_hashes_unchanged_and_quant_lab_has_no_trading_imports():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in PROTECTED_HASHES.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest().upper() == expected
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "src/ashare_agent/quant_lab").glob("*.py")
    )
    forbidden = ("paper_portfolio", "place_order", "submit_order", "live_trading_enabled")
    assert all(term not in source.lower() for term in forbidden)
