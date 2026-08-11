from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3

import pandas as pd
import pytest

from ashare_agent.data_center import DataCenter, DataCenterError
from ashare_agent.research_pipeline import ResearchPipelineResult


def pipeline_result() -> ResearchPipelineResult:
    """Build one complete deterministic pipeline result for persistence tests."""
    universe = pd.DataFrame([
        {"symbol": "600000.SH", "name": "浦发银行"},
        {"symbol": "600001.SH", "name": "测试股份"},
    ])
    snapshot = pd.DataFrame([
        {"symbol": "600000.SH", "last_price": 10.0, "turnover": 100_000_000, "volume": 1_000_000},
        {"symbol": "600001.SH", "last_price": 12.0, "turnover": 80_000_000, "volume": 800_000},
    ])
    history = pd.DataFrame([
        {"symbol": "600000.SH", "date": "2026-07-17", "close": 9.8, "turnover": 90_000_000},
        {"symbol": "600000.SH", "date": "2026-07-20", "close": 10.0, "turnover": 100_000_000},
        {"symbol": "600001.SH", "date": "2026-07-17", "close": 11.8, "turnover": 75_000_000},
        {"symbol": "600001.SH", "date": "2026-07-20", "close": 12.0, "turnover": 80_000_000},
    ])
    fundamentals = pd.DataFrame([
        {"symbol": "600000.SH", "available_at": "2026-06-30", "roe": 0.11},
        {"symbol": "600001.SH", "available_at": "2026-06-30", "roe": 0.09},
    ])
    features = snapshot.assign(
        momentum=[0.1, 0.08],
        volatility=[0.2, 0.25],
        max_drawdown=[-0.08, -0.1],
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


def test_data_center_writes_immutable_evidence_and_unique_run_ids(tmp_path):
    """Identical inputs share a data version while every research run remains unique."""
    center = DataCenter(tmp_path / "data_center")
    first = center.record_research(
        pipeline_result(),
        run_kind="formal_close_plan",
        metadata={"provider": "test", "preview": False, "ignored": "not persisted"},
    )
    second = center.record_research(
        pipeline_result(), run_kind="formal_close_plan", metadata={"provider": "test"}
    )

    assert first.run_id != second.run_id
    assert first.data_version == second.data_version
    expected_prefix = (
        f"2026-07-20_cross-sectional-v2.0.0_dv-{first.data_version}_"
    )
    assert first.run_id.startswith(expected_prefix)
    assert re.fullmatch(r"[0-9a-f]{8}", first.run_id.removeprefix(expected_prefix))

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == first.run_id
    assert manifest["data_version"] == first.data_version
    assert manifest["strategy_version"] == "cross-sectional-v2.0.0"
    assert manifest["factor_version"] == "cross-sectional-v1.0.0"
    assert manifest["data_model_version"] == "pangu-data-model-v2.0.0"
    assert manifest["metadata"] == {"preview": False, "provider": "test"}
    assert set(manifest["assets"]) == {
        "raw_security_universe",
        "raw_market_snapshot",
        "raw_price_history",
        "raw_fundamentals",
        "security_master",
        "market_data",
        "security_universe_pit",
        "market_data_pit",
        "financial_point_in_time",
        "feature_store",
        "factor_store",
        "final_ranking",
        "portfolio_weights",
    }
    for asset in manifest["assets"].values():
        content = gzip.decompress(center.root.joinpath(asset["path"]).read_bytes())
        assert hashlib.sha256(content).hexdigest() == asset["sha256"]

    assert {path.name for path in center.root.iterdir() if path.is_dir()} >= {
        "raw", "clean", "point_in_time", "features", "snapshots"
    }
    assert manifest["quality_checks"]["referential_integrity"] is True
    assert all(
        item["valid"] for item in manifest["quality_checks"]["models"].values()
    )

    with sqlite3.connect(center.catalog_path) as connection:
        run = connection.execute(
            "SELECT strategy_version, factor_version, data_source FROM research_runs WHERE run_id = ?",
            (first.run_id,),
        ).fetchone()
        models = connection.execute("SELECT COUNT(*) FROM data_models").fetchone()[0]
        assets = connection.execute(
            "SELECT COUNT(*) FROM data_assets WHERE run_id = ?", (first.run_id,)
        ).fetchone()[0]
    assert run == ("cross-sectional-v2.0.0", "cross-sectional-v1.0.0", "test")
    assert models == 5
    assert assets == first.asset_count

    latest = json.loads((tmp_path / "data_center" / "latest.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == second.run_id


def test_data_center_rejects_sensitive_fields_and_metadata(tmp_path):
    """Credentials must never enter Data Center assets or manifests."""
    center = DataCenter(tmp_path / "data_center")
    result = pipeline_result()
    result.raw_snapshot["api_key"] = "must-not-persist"
    with pytest.raises(DataCenterError, match="sensitive evidence fields"):
        center.record_research(result, run_kind="test")

    clean = pipeline_result()
    with pytest.raises(DataCenterError, match="must not contain credentials"):
        center.record_research(
            clean,
            run_kind="test",
            metadata={"api_token": "must-not-persist"},
        )
