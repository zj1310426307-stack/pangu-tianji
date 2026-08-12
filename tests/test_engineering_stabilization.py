from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ashare_agent.api.app import create_app
from pangu.backup import BackupService
from pangu.config import ConfigCenter
from pangu.core.exceptions import ConfigurationError
from pangu.engineering import EngineeringService
from pangu.observability import EngineeringEventStore, PanguLogger, configure_structured_logging
from pangu.version import get_version_manifest


SOURCE_ROOT = Path(__file__).resolve().parents[1]
WRITE_HEADERS = {"X-Ashare-Client": "local-dashboard"}


def make_engineering_project(tmp_path: Path) -> Path:
    """Create only the project evidence required by engineering services."""
    root = tmp_path / "project"
    shutil.copytree(SOURCE_ROOT / "config", root / "config")
    (root / "output" / "data_center").mkdir(parents=True)
    (root / "output" / "quant_lab").mkdir(parents=True)
    with sqlite3.connect(root / "output" / "data_center" / "catalog.sqlite3") as connection:
        connection.execute("CREATE TABLE research_runs(run_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO research_runs VALUES('run-test')")
    (root / "output" / "data_center" / "latest.json").write_text(
        json.dumps({"run_id": "run-test", "research_date": "2026-08-11"}), encoding="utf-8"
    )
    (root / "output" / "quant_lab" / "report.json").write_text("{}", encoding="utf-8")
    return root


def test_version_registry_is_deterministic_and_matches_production_contracts() -> None:
    """Regression layer: protected production constants must not silently drift."""
    from ashare_agent.factor_model import FACTOR_MODEL_VERSION
    from ashare_agent.research_pipeline import STRATEGY_VERSION

    first = get_version_manifest()
    second = get_version_manifest()
    assert first == second
    assert first.strategy_versions["production"] == STRATEGY_VERSION
    assert first.factor_versions["production"] == FACTOR_MODEL_VERSION
    assert len(first.manifest_hash) == 64


def test_config_center_layers_overrides_hashes_and_rejects_live_mode(tmp_path: Path) -> None:
    """Unit layer: layered config is immutable, versioned and fail-closed."""
    root = make_engineering_project(tmp_path)
    snapshot = ConfigCenter(
        root,
        environ={"PANGU__ENGINEERING__LOG_LEVEL": "WARNING"},
    ).load("development")
    assert snapshot.configuration["engineering"]["log_level"] == "WARNING"
    assert snapshot.environment_overrides == ("PANGU__ENGINEERING__LOG_LEVEL",)
    assert snapshot.config_version == "pangu-config-v1.0.0"
    assert len(snapshot.config_hash) == 64
    with pytest.raises(TypeError):
        snapshot.configuration["new"] = "forbidden"
    with pytest.raises(ConfigurationError, match="禁止启用实盘"):
        ConfigCenter(
            root,
            environ={"PANGU__SYSTEM__LIVE_TRADING_ENABLED": "true"},
        ).load("development")


def test_structured_event_is_redacted_and_database_constraints_are_non_trading(tmp_path: Path) -> None:
    """Unit/integration layer: log and DB copies share the safe event contract."""
    root = make_engineering_project(tmp_path)
    store = EngineeringEventStore(root / "output" / "engineering_health.db")
    logger = PanguLogger(configure_structured_logging(root / "output" / "logs" / "test.jsonl"), store)
    payload = logger.event(
        "INFO", "test", "credential_probe", "key sk-abcdefghijklmnop",
        extra={"api_key": "sk-should-not-appear", "safe": "visible"},
    )
    assert payload["extra"]["api_key"] == "***REDACTED***"
    assert "sk-" not in payload["message"]
    row = store.list_events(1)[0]
    assert row["can_trade"] is False and row["can_create_orders"] is False
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO system_events VALUES(
                'bad','2026-01-01','INFO','x','x',NULL,NULL,'trace','bad','{}',
                'schema',1,0)"""
            )


def test_health_center_is_read_only_persisted_and_version_grounded(tmp_path: Path) -> None:
    """Health layer: all four dimensions are explicit and execution remains disabled."""
    root = make_engineering_project(tmp_path)
    service = EngineeringService(root, model_status_provider=lambda: {"state": "connected", "model": "test"})
    result = service.run_health()
    assert set(result["components"]) == {"data", "database", "ai", "strategy"}
    assert result["components"]["strategy"]["score"] == 100
    assert result["can_trade"] is False
    assert result["can_create_orders"] is False
    assert result["can_modify_strategy"] is False
    assert len(result["evidence_hash"]) == 64
    assert service.health.latest()["health_id"] == result["health_id"]


def test_backup_creates_non_overwriting_verified_manifest_without_secrets(tmp_path: Path) -> None:
    """Integration layer: copied evidence is checksummed and source content is unchanged."""
    root = make_engineering_project(tmp_path)
    service = EngineeringService(root, model_status_provider=lambda: {"state": "disabled"})
    source = root / "output" / "data_center" / "latest.json"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = service.create_backup()
    destination = root / result["backup_path"]
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert manifest["file_count"] == len(manifest["files"])
    assert all(".env" not in item["path"] for item in manifest["files"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    for item in manifest["files"]:
        path = destination / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_engineering_api_contract_and_local_write_boundary(tmp_path: Path) -> None:
    """API layer: generated operations are present and writes remain loopback protected."""
    from test_api import make_project

    root = make_project(tmp_path)
    app = create_app(root)
    paths = app.openapi()["paths"]
    assert "/api/v1/engineering" in paths
    assert paths["/api/v1/engineering"]["get"]["operationId"] == "get_engineering_dashboard"
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/engineering").json()
        assert dashboard["can_trade"] is False
        assert client.post("/api/v1/engineering/health/run").status_code == 403
        checked = client.post("/api/v1/engineering/health/run", headers=WRITE_HEADERS)
        assert checked.status_code == 200
        assert checked.json()["can_create_orders"] is False


def test_pangu_engineering_package_has_no_execution_dependency_or_swallowed_exception() -> None:
    """Safety regression: engineering infrastructure cannot depend on order modules."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in (SOURCE_ROOT / "src" / "pangu").rglob("*.py"))
    assert "paper_portfolio" not in text.lower()
    assert "place_order" not in text
    assert "submit_order" not in text
    assert "except Exception:\n                pass" not in text


def test_generated_client_and_frontend_use_openapi_operations_only() -> None:
    """Frontend layer: new endpoints are generated and handwritten code has no paths."""
    client = (SOURCE_ROOT / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    app = (SOURCE_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for operation in (
        "get_engineering_dashboard", "run_engineering_health", "create_engineering_backup",
    ):
        assert f'"{operation}"' in client
        assert operation in app
    assert "fetch(" not in app
    assert '"/api/' not in app and "'/api/" not in app
