from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from ashare_agent.data_center import DataCenter
from ashare_agent.data_monitor import DataIntelligenceService, DataQualityGateError
from ashare_agent.data_monitor.anomaly_detector import DataAnomalyDetector
from ashare_agent.data_monitor.contracts import DATA_HEALTH_WEIGHTS
from ashare_agent.data_monitor.quality_monitor import DataQualityMonitor
from ashare_agent.research_pipeline import ResearchPipelineResult


def _pipeline_result() -> ResearchPipelineResult:
    """Build a small but complete point-in-time research result."""
    universe = pd.DataFrame(
        [
            {"symbol": "600000.SH", "name": "浦发银行"},
            {"symbol": "600001.SH", "name": "测试股份"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "last_price": 10.0,
                "turnover": 100_000_000,
                "volume": 1_000_000,
            },
            {
                "symbol": "600001.SH",
                "last_price": 12.0,
                "turnover": 80_000_000,
                "volume": 800_000,
            },
        ]
    )
    history = pd.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "date": "2026-07-17",
                "close": 9.8,
                "turnover": 90_000_000,
            },
            {
                "symbol": "600000.SH",
                "date": "2026-07-20",
                "close": 10.0,
                "turnover": 100_000_000,
            },
            {
                "symbol": "600001.SH",
                "date": "2026-07-17",
                "close": 11.8,
                "turnover": 75_000_000,
            },
            {
                "symbol": "600001.SH",
                "date": "2026-07-20",
                "close": 12.0,
                "turnover": 80_000_000,
            },
        ]
    )
    fundamentals = pd.DataFrame(
        [
            {"symbol": "600000.SH", "available_at": "2026-06-30", "roe": 0.11},
            {"symbol": "600001.SH", "available_at": "2026-06-30", "roe": 0.09},
        ]
    )
    features = snapshot.assign(
        momentum=[0.1, 0.08], volatility=[0.2, 0.25], max_drawdown=[-0.08, -0.1]
    )
    factors = features.assign(score=[81.0, 76.0])
    ranked = factors.assign(rank=[1, 2])
    return ResearchPipelineResult(
        strategy_version="cross-sectional-v2.0.0",
        factor_model_version="cross-sectional-v1.0.0",
        factor_contract_hash="contract-hash",
        as_of=pd.Timestamp("2026-07-20"),
        raw_universe=universe.rename(columns={"symbol": "thscode"}),
        raw_snapshot=snapshot.rename(columns={"symbol": "thscode"}),
        raw_history=history,
        raw_fundamentals=fundamentals,
        universe=universe,
        snapshot=snapshot,
        features=features,
        factor_results=factors,
        ranked=ranked,
        targets=["600000.SH"],
        target_weights={"600000.SH": 0.2},
        portfolio_rejections={"600001.SH": "组合容量已满"},
        return_history={},
        stage_counts={
            "security_universe": 2,
            "data_snapshot": 2,
            "feature_calculation": 2,
            "factor_score": 2,
            "ranking": 2,
            "portfolio_construction": 1,
        },
        data_quality={"financial_coverage": 1.0},
    )


def _verified_run(tmp_path: Path, *, preview: bool = False):
    center = DataCenter(tmp_path / "output" / "data_center")
    record = center.record_research(
        _pipeline_result(),
        run_kind="intraday_preview" if preview else "formal_close_plan",
        metadata={
            "provider": "ths_finance",
            "observed_at": (
                "2026-07-20T10:05:00+08:00"
                if preview
                else "2026-07-20T15:05:00+08:00"
            ),
            "preview": preview,
        },
    )
    center._mark_verified(record.run_id)
    return center, record


def test_data_health_score_has_fixed_weights_and_catalog(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path)
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)

    health = service.evaluate_run(record.run_id, enforce=True)

    assert health["score"] == 100.0
    assert health["status"] == "NORMAL"
    assert health["publish_allowed"] is True
    assert health["weights"] == DATA_HEALTH_WEIGHTS
    assert {
        name: item["weight"] for name, item in health["components"].items()
    } == DATA_HEALTH_WEIGHTS
    assert health["can_trade"] is False
    assert health["can_modify_historical_data"] is False
    catalog = service.catalog()["items"]
    assert catalog[0]["data_version"] == record.data_version
    assert catalog[0]["source"] == "ths_finance"
    assert catalog[0]["manifest_hash"] == hashlib.sha256(
        record.manifest_path.read_bytes()
    ).hexdigest()
    assert catalog[0]["coverage"] == 1.0


def test_preview_is_never_publishable_even_when_data_is_healthy(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path, preview=True)
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)

    health = service.evaluate_run(record.run_id, preview=True)

    assert health["status"] == "NORMAL"
    assert health["preview"] is True
    assert health["publish_allowed"] is False


def test_hash_tamper_creates_blocked_incident_and_enforces_gate(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path)
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    asset_path = center.root / manifest["assets"]["raw_market_snapshot"]["path"]
    asset_path.write_bytes(gzip.compress(b'[{"thscode":"600000.SH","last_price":999}]'))
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)

    with pytest.raises(DataQualityGateError) as captured:
        service.evaluate_run(record.run_id, enforce=True)

    health = captured.value.assessment
    assert health["status"] == "BLOCKED"
    assert health["publish_allowed"] is False
    assert any(item["code"] == "ASSET_HASHES_VALID" for item in health["issues"])
    incidents = service.incidents()["items"]
    assert any(item["level"] == "BLOCKED" for item in incidents)


def test_manifest_asset_index_tamper_is_detected_against_data_center_catalog(
    tmp_path: Path,
) -> None:
    center, record = _verified_run(tmp_path)
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    metadata = manifest["assets"]["raw_market_snapshot"]
    asset_path = center.root / metadata["path"]
    changed = b'[{"thscode":"600000.SH","last_price":10,"volume":100}]'
    asset_path.write_bytes(gzip.compress(changed))
    metadata["sha256"] = hashlib.sha256(changed).hexdigest()
    metadata["rows"] = 1
    record.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)

    health = service.evaluate_run(record.run_id)

    assert health["status"] == "BLOCKED"
    assert any(item["code"] == "CATALOG_ASSETS_VALID" for item in health["issues"])
    assert health["components"]["consistency"]["checks"]["catalog_assets_valid"] is False


def test_incident_acknowledgement_cannot_release_publication_gate(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path)
    manifest = json.loads(record.manifest_path.read_text(encoding="utf-8"))
    asset_path = center.root / manifest["assets"]["factor_store"]["path"]
    asset_path.write_bytes(gzip.compress(b"[]"))
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)
    blocked = service.evaluate_run(record.run_id)
    incident = service.incidents(status="OPEN")["items"][0]

    acknowledged = service.acknowledge_incident(incident["incident_id"])
    persisted = service.health(record.run_id)

    assert acknowledged["status"] == "ACKNOWLEDGED"
    assert acknowledged["can_bypass_gate"] is False
    assert blocked["publish_allowed"] is False
    assert persisted["publish_allowed"] is False
    assert persisted["status"] == "BLOCKED"


def test_synthetic_market_and_factor_anomalies_are_deterministic() -> None:
    quality = DataQualityMonitor().evaluate(
        {"assets": {name: {} for name in (
            "raw_security_universe", "raw_market_snapshot", "raw_price_history",
            "raw_fundamentals", "security_master", "market_data_pit",
            "financial_point_in_time", "feature_store", "factor_store",
            "final_ranking", "portfolio_weights",
        )}},
        {
            "raw_security_universe": [{"thscode": "600000.SH"}],
            "raw_market_snapshot": [
                {"thscode": "600000.SH", "last_price": 10, "volume": 100,
                 "suspended": True}
            ],
            "security_master": [
                {"symbol": "600000.SH"}, {"symbol": "600000.SH"}
            ],
            "market_data_pit": [
                {"symbol": "600000.SH", "last_price": -1, "volume": -2}
            ],
            "raw_price_history": [],
            "financial_point_in_time": [
                {"symbol": "600000.SH", "available_at": "2026-06-30",
                 "net_profit_yoy_growth_ratio": 20_000}
            ],
            "feature_store": [{"symbol": "600000.SH"}],
            "factor_store": [{"symbol": "600000.SH", "score": 500}],
            "final_ranking": [{"symbol": "600000.SH"}],
        },
        {},
    )
    anomaly = DataAnomalyDetector().evaluate(
        {"research_date": "2026-07-20"},
        {
            "raw_price_history": [
                {"symbol": "600000.SH", "date": "2026-07-17", "close": 10},
                {"symbol": "600000.SH", "date": "2026-07-20", "close": 100},
            ],
            "financial_point_in_time": [
                {"symbol": "600000.SH", "available_at": "2026-07-21"}
            ],
            "factor_store": [{"symbol": "600000.SH", "score": 500}],
            "security_master": [{"symbol": "600000.SH"}],
        },
    )

    quality_codes = {item["code"] for item in quality["issues"]}
    anomaly_codes = {item["code"] for item in anomaly["issues"]}
    assert {
        "DUPLICATE_PRIMARY_KEY", "INVALID_PRICE", "INVALID_VOLUME",
        "SUSPENSION_STATUS_CONFLICT", "ROE_MISSING", "EXTREME_FINANCIAL_VALUE",
    } <= quality_codes
    assert {
        "EXTREME_PRICE_JUMP", "FUTURE_FINANCIAL_DATA", "INVALID_FACTOR_SCORE"
    } <= anomaly_codes
    assert quality["blocking"] is True
    assert anomaly["blocking"] is True


def test_lineage_connects_source_assets_factors_and_experiment_reports(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path)
    quant_db = tmp_path / "output" / "quant_lab" / "experiments.sqlite3"
    quant_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(quant_db) as connection:
        connection.executescript(
            """
            CREATE TABLE experiments(experiment_id TEXT PRIMARY KEY, spec_json TEXT);
            CREATE TABLE factor_reports(report_id TEXT, experiment_id TEXT);
            CREATE TABLE robustness_reports(report_id TEXT, experiment_id TEXT);
            """
        )
        connection.execute(
            "INSERT INTO experiments VALUES(?,?)",
            ("exp-1", json.dumps({"data_center_run_ids": [record.run_id]})),
        )
        connection.execute("INSERT INTO factor_reports VALUES('factor-1','exp-1')")
        connection.execute("INSERT INTO robustness_reports VALUES('robust-1','exp-1')")
    agent_db = tmp_path / "output" / "agent.db"
    with sqlite3.connect(agent_db) as connection:
        connection.execute(
            "CREATE TABLE ai_research_reports(report_id TEXT, experiment_id TEXT)"
        )
        connection.execute("INSERT INTO ai_research_reports VALUES('ai-1','exp-1')")
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)
    service.evaluate_run(record.run_id)

    graph = service.lineage(record.run_id)
    node_types = {item["node_type"] for item in graph["nodes"]}
    relations = {item["relation"] for item in graph["edges"]}

    assert {
        "data_source", "data_version", "research_run", "data_asset", "experiment",
        "factor_report", "robustness_report", "ai_research_report",
    } <= node_types
    assert {"produces", "materializes", "feeds", "ranks", "constructs", "used_by"} <= relations
    assert graph["persisted_edges"]
    assert graph["can_trade"] is False


def test_monitor_store_database_enforces_zero_execution_capability(tmp_path: Path) -> None:
    center, record = _verified_run(tmp_path)
    service = DataIntelligenceService(tmp_path, data_center_root=center.root)
    service.evaluate_run(record.run_id)
    with sqlite3.connect(service.store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE data_health SET can_trade=1 WHERE run_id=?", (record.run_id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE data_catalog SET can_modify_historical_data=1 WHERE run_id=?",
                (record.run_id,),
            )
