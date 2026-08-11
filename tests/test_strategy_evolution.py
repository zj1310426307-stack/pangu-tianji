from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from ashare_agent.quant_lab.contracts import sha256_json
from ashare_agent.strategy_evolution import (
    EvolutionArtifactError,
    EvolutionEvidenceError,
    EvolutionStateError,
    StrategyEvolutionService,
    StrategyObservation,
)
from ashare_agent.strategy_evolution.factor_drift import FactorDriftMonitor
from ashare_agent.strategy_evolution.strategy_monitor import StrategyHealthMonitor


FACTORS = ("value", "quality", "growth", "momentum", "trend", "low_risk", "liquidity")


class FakeValidationRegistry:
    """Expose immutable validation identities required by evolution tests."""

    def __init__(self) -> None:
        self.reviews = [
            {
                "review_id": "review-2", "strategy_id": "pangu-mf",
                "strategy_version": "v2.0.0", "report_hash": "2" * 64,
            },
            {
                "review_id": "review-1", "strategy_id": "pangu-mf",
                "strategy_version": "v2.0.0", "report_hash": "1" * 64,
            },
            {
                "review_id": "review-b", "strategy_id": "pangu-mf",
                "strategy_version": "v2.1.0", "report_hash": "3" * 64,
            },
        ]

    def version(self, strategy_id: str, version: str) -> dict:
        if strategy_id != "pangu-mf" or version not in {"v2.0.0", "v2.1.0"}:
            raise KeyError((strategy_id, version))
        suffix = "a" if version == "v2.0.0" else "b"
        return {
            "version_hash": suffix * 64,
            "spec": {
                "code_hash": ("c" if version == "v2.0.0" else "d") * 64,
                "parameter_hash": ("e" if version == "v2.0.0" else "f") * 64,
                "factor_version": "factor-v2",
                "dataset_version": "dc-v2",
                "dataset_label": "POINT_IN_TIME",
            },
        }

    def strategy(self, strategy_id: str) -> dict:
        if strategy_id != "pangu-mf":
            raise KeyError(strategy_id)
        return {
            "strategy_id": strategy_id, "name": "盘古七因子",
            "current_state": "PAPER_TRADING", "active_version": "v2.0.0",
        }

    def list_reviews(self, strategy_id: str | None = None, limit: int = 200) -> list[dict]:
        return [item for item in self.reviews if not strategy_id or item["strategy_id"] == strategy_id][:limit]

    def list_strategies(self) -> list[dict]:
        return [self.strategy("pangu-mf")]


class FakeValidationService:
    """Keep tests independent from the protected Validation Gate implementation."""

    def __init__(self) -> None:
        self.registry = FakeValidationRegistry()
        self.experiment_registry = None


class FakeEvolutionReader:
    """Return named sealed observations without exposing any mutation method."""

    def __init__(self, observations: dict[str, StrategyObservation]) -> None:
        self.observations = observations

    def read(self, review_id: str | None = None) -> StrategyObservation:
        if review_id not in self.observations:
            raise EvolutionEvidenceError("review unavailable")
        return self.observations[str(review_id)]


def _factors(*, ic: float = 0.04, icir: float = 0.8, contribution: float = 0.12) -> dict:
    return {
        name: {
            "mean": 50.0 + index, "std": 10.0, "ic": ic,
            "icir": icir, "hit_rate": 0.58, "contribution": contribution,
        }
        for index, name in enumerate(FACTORS)
    }


def _observation(
    review_id: str,
    *,
    version: str = "v2.0.0",
    annual_return: float = 0.15,
    sharpe: float = 1.2,
    win_rate: float = 0.58,
    drawdown: float = 0.10,
    factors: dict | None = None,
) -> StrategyObservation:
    evidence_hash = sha256_json({"review_id": review_id, "version": version})
    return StrategyObservation(
        strategy_id="pangu-mf",
        strategy_version=version,
        review_id=review_id,
        observed_at=f"2026-08-{10 if review_id.endswith('1') else 11}T15:30:00+08:00",
        dataset_label="POINT_IN_TIME",
        performance={
            "annual_return": annual_return, "total_return": annual_return,
            "sharpe": sharpe, "win_rate": win_rate,
        },
        risk={"max_drawdown": drawdown, "volatility": 0.18},
        factors=factors or _factors(),
        execution={"turnover": 1.1, "cost_ratio": 0.03, "execution_deviation": 0.01},
        environment={"availability": "AVAILABLE", "regime_source": "registered"},
        evidence_ids=(f"E-VALIDATION-{review_id}", f"E-FACTOR-{review_id}"),
        evidence_hash=evidence_hash,
    )


def _service(tmp_path: Path, *, second: StrategyObservation | None = None) -> StrategyEvolutionService:
    observations = {
        "review-1": _observation("review-1"),
        "review-2": second or _observation("review-2", annual_return=0.16, sharpe=1.25),
        "review-b": _observation("review-b", version="v2.1.0", annual_return=0.18, sharpe=1.35),
    }
    return StrategyEvolutionService(
        tmp_path,
        strategy_validation_service=FakeValidationService(),
        evidence_reader=FakeEvolutionReader(observations),
    )


def test_health_weights_are_fixed_and_missing_components_are_not_renormalized():
    monitor = StrategyHealthMonitor()
    observation = _observation("review-1").as_dict()
    observation["risk"] = {"max_drawdown": None, "volatility": None}
    observation["execution"] = {"turnover": None, "cost_ratio": None, "execution_deviation": None}
    observation["environment"] = {"availability": "UNAVAILABLE"}
    result = monitor.evaluate(observation)
    assert result["weights"] == {
        "performance": 0.25, "risk": 0.20, "factor": 0.25,
        "execution": 0.15, "environment": 0.15,
    }
    assert result["coverage"] == pytest.approx(0.25)
    assert result["score"] is None
    assert result["partial_score"] <= 25


def test_factor_drift_detects_ic_and_contribution_decay_without_weight_change():
    current = _factors(ic=0.0, icir=0.1, contribution=-0.2)
    result = FactorDriftMonitor().evaluate(current, _factors())
    momentum = next(item for item in result["factors"] if item["factor_name"] == "momentum")
    assert result["status"] == "DECAYING"
    assert momentum["status"] == "DECAYING"
    assert momentum["can_modify_weight"] is False
    assert result["automatic_weight_change"] is False


def test_first_evaluation_builds_baseline_and_retry_is_idempotent(tmp_path):
    service = _service(tmp_path)
    branch = service.import_branch(strategy_id="pangu-mf", version="v2.0.0", branch_name="main")
    assert branch["lifecycle_state"] == "DRAFT"
    first = service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    repeated = service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    assert first["health"]["health_id"] == repeated["health"]["health_id"]
    assert first["health"]["decay"]["status"] == "BASELINE_BUILDING"
    assert service.dashboard()["counts"]["health_snapshots"] == 1
    assert first["can_trade"] is False and first["can_modify_strategy"] is False


def test_second_evaluation_detects_strategy_decay(tmp_path):
    degraded = _observation(
        "review-2", annual_return=0.02, sharpe=0.4, win_rate=0.44,
        drawdown=0.20, factors=_factors(ic=0.0, icir=0.1, contribution=-0.2),
    )
    service = _service(tmp_path, second=degraded)
    service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    result = service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-2")
    assert result["health"]["decay"]["status"] == "DECAYING"
    assert result["health"]["factor_drift"]["status"] == "DECAYING"
    assert result["health"]["status"] == "DECAYING"
    report = service.report(result["report_id"])
    assert report["sections"]["07_strategy_observer"]["research_questions"]
    assert report["ai_strategy_observer_interface"]["can_launch_experiment"] is False


def test_daily_observer_is_idempotent_and_never_executes(tmp_path):
    service = _service(tmp_path)
    first = service.observe_registered_strategies()
    repeated = service.observe_registered_strategies()
    assert first["counts"] == {"observed": 1, "unavailable": 0}
    assert repeated["results"][0]["health_id"] == first["results"][0]["health_id"]
    assert service.dashboard()["counts"]["health_snapshots"] == 1
    assert first["used_for_execution"] is False
    assert first["can_trade"] is False and first["can_launch_experiment"] is False


def test_strategy_versions_compare_without_automatic_winner(tmp_path):
    service = _service(tmp_path)
    service.import_branch(strategy_id="pangu-mf", version="v2.0.0", branch_name="main")
    service.import_branch(
        strategy_id="pangu-mf", version="v2.1.0",
        branch_name="quality-enhance", parent_version="v2.0.0",
    )
    service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    service.evaluate(strategy_id="pangu-mf", version="v2.1.0", review_id="review-b")
    comparison = service.compare(
        strategy_id="pangu-mf", version_a="v2.0.0", version_b="v2.1.0"
    )
    assert comparison["result"] == "DESCRIPTIVE_COMPARISON_ONLY"
    assert comparison["automatic_winner_selected"] is False
    assert comparison["can_replace_production_strategy"] is False


def test_branch_identity_is_immutable(tmp_path):
    service = _service(tmp_path)
    first = service.import_branch(
        strategy_id="pangu-mf", version="v2.0.0", branch_name="main"
    )
    repeated = service.import_branch(
        strategy_id="pangu-mf", version="v2.0.0", branch_name="main"
    )
    assert repeated["spec_hash"] == first["spec_hash"]
    with pytest.raises(EvolutionArtifactError):
        service.import_branch(strategy_id="pangu-mf", version="v2.0.0", branch_name="changed")


def test_lifecycle_is_sequential_human_and_never_live(tmp_path):
    service = _service(tmp_path)
    service.import_branch(strategy_id="pangu-mf", version="v2.0.0", branch_name="main")
    service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-2")
    with pytest.raises(EvolutionStateError):
        service.request_transition(
            strategy_id="pangu-mf", version="v2.0.0", target_state="VALIDATED",
            requested_by="研究员", reason="不能跳过研究阶段",
        )
    for target in (
        "RESEARCH", "VALIDATED", "PAPER_RUNNING", "PRODUCTION_CANDIDATE",
        "DEPRECATED", "RETIRED",
    ):
        request = service.request_transition(
            strategy_id="pangu-mf", version="v2.0.0", target_state=target,
            requested_by="王研究员", reason=f"人工核对全部封存证据后申请进入{target}",
        )
        decided = service.decide_transition(
            request["request_id"], approved=True, actor="李委员",
            reason=f"人工确认研究治理条件并批准进入{target}",
        )
        assert decided["production_strategy_changed"] is False
        assert decided["can_trade"] is False
    branch = service.store.branch("pangu-mf", "v2.0.0")
    assert branch["lifecycle_state"] == "RETIRED"
    assert "LIVE" not in branch["lifecycle_state"]


def test_ai_or_system_cannot_govern_lifecycle(tmp_path):
    service = _service(tmp_path)
    service.import_branch(strategy_id="pangu-mf", version="v2.0.0", branch_name="main")
    with pytest.raises(EvolutionStateError, match="人工"):
        service.request_transition(
            strategy_id="pangu-mf", version="v2.0.0", target_state="RESEARCH",
            requested_by="AI", reason="模型不得申请生命周期变化",
        )


def test_artifact_tamper_is_detected(tmp_path):
    service = _service(tmp_path)
    result = service.evaluate(
        strategy_id="pangu-mf", version="v2.0.0", review_id="review-1"
    )
    evaluation_id = result["health"]["evaluation_id"]
    path = service.artifacts.root / "pangu-mf" / evaluation_id / "strategy_health_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sections"]["03_strategy_health"]["score"] = 100
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvolutionArtifactError):
        service.report(result["report_id"])


def test_database_rejects_strategy_or_execution_capabilities(tmp_path):
    service = _service(tmp_path)
    service.evaluate(strategy_id="pangu-mf", version="v2.0.0", review_id="review-1")
    with sqlite3.connect(service.store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE strategy_health SET can_modify_strategy=1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE evolution_reports SET ai_can_launch_experiment=1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE strategy_registry SET can_trade=1")


def test_daily_runner_and_windows_task_contain_no_secret_or_trade_command():
    root = Path(__file__).resolve().parents[1]
    runner = (root / "strategy_evolution_daily.py").read_text(encoding="utf-8")
    installer = (root / "安装策略进化观察任务.cmd").read_text(encoding="utf-8")
    command = (root / "运行策略进化观察.cmd").read_text(encoding="utf-8")
    combined = "\n".join((runner, installer, command)).lower()
    assert "observe_registered_strategies" in runner
    assert "16:10" in installer
    assert "api_key" not in combined and "deepseek" not in combined
    assert "place_order" not in combined and "submit_order" not in combined
    assert "paper_portfolio" not in combined and "broker" not in combined
