from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .alert_manager import DataAlertManager
from .anomaly_detector import DataAnomalyDetector
from .consistency_monitor import DataConsistencyMonitor
from .contracts import (
    DATA_HEALTH_WEIGHTS,
    DATA_INTELLIGENCE_VERSION,
    DataIntelligenceError,
    DataQualityGateError,
    content_hash,
    safety_contract,
)
from .coverage_monitor import DataCoverageMonitor
from .freshness_monitor import DataFreshnessMonitor
from .lineage import DataLineageService
from .quality_monitor import DataQualityMonitor
from .store import DataIntelligenceStore


class DataIntelligenceService:
    """Monitor immutable Data Center runs and enforce the formal publication gate."""

    def __init__(
        self,
        project_root: Path,
        *,
        data_center_root: Path | None = None,
        store: DataIntelligenceStore | None = None,
    ) -> None:
        # Keep ASCII junctions intact on Windows while still using absolute paths.
        self.root = Path(os.path.abspath(project_root))
        self.data_center_root = Path(
            os.path.abspath(data_center_root or self.root / "output" / "data_center")
        )
        self.store = store or DataIntelligenceStore(
            self.root / "output" / "data_intelligence.db"
        )
        self.quality = DataQualityMonitor()
        self.freshness = DataFreshnessMonitor()
        self.coverage = DataCoverageMonitor()
        self.anomaly = DataAnomalyDetector()
        self.consistency = DataConsistencyMonitor()
        self.alerts = DataAlertManager(self.store)
        self.lineage_service = DataLineageService(self.root)

    def evaluate_run(
        self,
        run_id: str,
        *,
        preview: bool | None = None,
        enforce: bool = False,
    ) -> dict[str, Any]:
        """Evaluate, persist and optionally enforce one immutable Data Center run."""
        loaded = self._load_run(run_id)
        manifest = loaded["manifest"]
        inferred_preview = str(manifest.get("run_kind")) == "intraday_preview"
        is_preview = inferred_preview if preview is None else bool(preview)
        baseline_assets = self._baseline_assets(manifest)
        components = {
            "completeness": self.quality.evaluate(
                manifest, loaded["assets"], loaded["load_errors"]
            ),
            "freshness": self.freshness.evaluate(manifest, preview=is_preview),
            "coverage": self.coverage.evaluate(manifest, loaded["assets"]),
            "anomaly": self.anomaly.evaluate(manifest, loaded["assets"], baseline_assets),
            "consistency": self.consistency.evaluate(
                manifest,
                catalog_verified=bool(loaded["catalog_verified"]),
                manifest_hash_valid=bool(loaded["manifest_hash_valid"]),
                catalog_assets_valid=bool(loaded["catalog_assets_valid"]),
                asset_hashes_valid=not bool(loaded["load_errors"]),
            ),
        }
        issues = [item for value in components.values() for item in value["issues"]]
        score = round(sum(float(value["contribution"]) for value in components.values()), 2)
        blocking = any(bool(item.get("blocking")) for item in issues)
        if blocking or score < 60:
            status = "BLOCKED"
        elif any(item["level"] == "ERROR" for item in issues) or score < 70:
            status = "ERROR"
        elif issues or score < 85:
            status = "WARNING"
        else:
            status = "NORMAL"
        publish_allowed = bool(not is_preview and status == "NORMAL")
        assessment = {
            "service_version": DATA_INTELLIGENCE_VERSION,
            "run_id": run_id,
            "data_version": str(manifest.get("data_version") or "unknown"),
            "research_date": str(manifest.get("research_date") or ""),
            "preview": is_preview,
            "score": score,
            "status": status,
            "components": components,
            "issues": issues,
            "blocking": blocking,
            "publish_allowed": publish_allowed,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "weights": dict(DATA_HEALTH_WEIGHTS),
            "evidence": {
                "manifest_hash": loaded["manifest_hash"],
                "data_center_verified": bool(loaded["catalog_verified"]),
                "baseline_run_id": (
                    baseline_assets.get("_run_id") if baseline_assets else None
                ),
            },
            "safety": safety_contract(),
            **safety_contract(),
        }
        lineage = self.lineage_service.build(manifest)
        coverage_ratio = float(components["coverage"]["score"]) / 100.0
        saved = self.store.save_assessment(
            assessment,
            {
                "source": str(manifest.get("data_source") or "unknown"),
                "manifest_hash": loaded["manifest_hash"],
                "coverage": coverage_ratio,
            },
            lineage["edges"],
        )
        result = {**assessment, **{
            key: saved[key] for key in ("health_id", "evidence_hash", "created_time")
            if key in saved
        }}
        if enforce and not publish_allowed:
            raise DataQualityGateError(result)
        return result

    def evaluate_latest(self) -> dict[str, Any]:
        """Explicitly evaluate the newest immutable Data Center run."""
        latest = self._latest_reference()
        if not latest:
            raise DataIntelligenceError("Data Center尚无可评估run")
        return self.evaluate_run(str(latest["run_id"]))

    def dashboard(self) -> dict[str, Any]:
        """Return persisted monitoring state without evaluating or modifying source data."""
        latest_ref = self._latest_reference()
        latest_health = self.store.latest_health()
        if latest_ref and latest_health and latest_health.get("run_id") != latest_ref.get("run_id"):
            current_health = None
        else:
            current_health = latest_health
        incidents = self.alerts.list(limit=50)
        catalog = self.store.catalog(limit=30)
        latest_lineage = (
            self.lineage(str(latest_ref["run_id"])) if latest_ref and current_health else None
        )
        return {
            "service_version": DATA_INTELLIGENCE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_center": {
                "available": bool(latest_ref),
                "latest": latest_ref,
                "root": "output/data_center",
                "immutable": True,
            },
            "health": current_health or {
                "status": "NOT_EVALUATED", "score": None, "components": {},
                "issues": [], "publish_allowed": False, "blocking": True,
            },
            "incidents": incidents,
            "catalog": catalog,
            "lineage": latest_lineage,
            "counts": {
                "catalog_versions": len(catalog),
                "open_incidents": incidents["counts"]["open"],
                "lineage_nodes": (latest_lineage or {}).get("counts", {}).get("nodes", 0),
                "lineage_edges": (latest_lineage or {}).get("counts", {}).get("edges", 0),
            },
            "safety": safety_contract(),
            **safety_contract(),
        }

    def health(self, run_id: str | None = None) -> dict[str, Any]:
        """Read one persisted health snapshot; GET never triggers evaluation."""
        value = self.store.health(run_id) if run_id else self.store.latest_health()
        if value is None:
            raise KeyError(run_id or "latest")
        return {**value, "safety": safety_contract(), **safety_contract()}

    def incidents(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """List warning, error and blocked incidents."""
        return {**self.alerts.list(status=status, limit=limit), "safety": safety_contract()}

    def acknowledge_incident(self, incident_id: str) -> dict[str, Any]:
        """Acknowledge visibility only; this cannot release a blocked publication."""
        return {**self.alerts.acknowledge(incident_id), "safety": safety_contract()}

    def catalog(self, limit: int = 100) -> dict[str, Any]:
        """List immutable data versions indexed by the monitoring platform."""
        items = self.store.catalog(limit)
        return {"items": items, "count": len(items), "safety": safety_contract()}

    def lineage(self, run_id: str) -> dict[str, Any]:
        """Return a current read-only graph plus the persisted edge audit."""
        loaded = self._load_run(run_id, load_assets=False)
        graph = self.lineage_service.build(loaded["manifest"])
        return {
            **graph,
            "persisted_edges": self.store.lineage(run_id),
            "safety": safety_contract(),
            **safety_contract(),
        }

    def _latest_reference(self) -> dict[str, Any] | None:
        """Read the Data Center latest pointer without resolving or modifying it."""
        path = self.data_center_root / "latest.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise DataIntelligenceError("Data Center latest.json无法读取") from exc
        return dict(value) if isinstance(value, Mapping) else None

    def _load_run(self, run_id: str, *, load_assets: bool = True) -> dict[str, Any]:
        """Read a cataloged manifest and verify every referenced content-addressed asset."""
        catalog_path = self.data_center_root / "catalog.sqlite3"
        if not catalog_path.exists():
            raise DataIntelligenceError("Data Center catalog不存在")
        try:
            with sqlite3.connect(catalog_path) as connection:
                row = connection.execute(
                    """SELECT manifest_path,verified,data_version,research_date,run_kind
                       FROM research_runs WHERE run_id=?""", (run_id,),
                ).fetchone()
                asset_rows = connection.execute(
                    """SELECT asset_name,layer,model_name,model_version,asset_path,sha256,row_count
                       FROM data_assets WHERE run_id=? ORDER BY asset_name""",
                    (run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DataIntelligenceError("Data Center catalog无法读取") from exc
        if row is None:
            raise KeyError(run_id)
        manifest_path = self._confined(self.data_center_root / str(row[0]))
        try:
            raw_manifest = manifest_path.read_bytes()
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataIntelligenceError("Data Center manifest无法读取") from exc
        manifest_hash = hashlib.sha256(raw_manifest).hexdigest()
        manifest_hash_valid = (
            str(manifest.get("run_id")) == run_id
            and str(manifest.get("data_version")) == str(row[2])
            and str(manifest.get("research_date")) == str(row[3])
            and str(manifest.get("run_kind")) == str(row[4])
        )
        indexed_assets = {
            str(item[0]): {
                "layer": str(item[1]),
                "model": str(item[2]),
                "model_version": str(item[3]),
                "path": str(item[4]),
                "sha256": str(item[5]),
                "rows": int(item[6]),
            }
            for item in asset_rows
        }
        manifest_assets = dict(manifest.get("assets") or {})
        catalog_assets_valid = set(indexed_assets) == set(manifest_assets) and all(
            indexed_assets[name] == {
                "layer": str(manifest_assets[name].get("layer") or ""),
                "model": str(manifest_assets[name].get("model") or ""),
                "model_version": str(manifest_assets[name].get("model_version") or ""),
                "path": str(manifest_assets[name].get("path") or ""),
                "sha256": str(manifest_assets[name].get("sha256") or ""),
                "rows": int(manifest_assets[name].get("rows") or 0),
            }
            for name in indexed_assets
        )
        assets: dict[str, Any] = {}
        load_errors: dict[str, str] = {}
        if load_assets:
            for name, metadata in (manifest.get("assets") or {}).items():
                try:
                    asset_path = self._confined(self.data_center_root / str(metadata["path"]))
                    content = gzip.decompress(asset_path.read_bytes())
                    if hashlib.sha256(content).hexdigest() != str(metadata["sha256"]):
                        raise ValueError("SHA256_MISMATCH")
                    assets[str(name)] = json.loads(content.decode("utf-8"))
                except Exception as exc:  # bounded into safe diagnostic text
                    load_errors[str(name)] = type(exc).__name__
        return {
            "manifest": manifest,
            "manifest_hash": manifest_hash,
            "manifest_hash_valid": manifest_hash_valid,
            "catalog_assets_valid": catalog_assets_valid,
            "catalog_verified": bool(row[1]),
            "assets": assets,
            "load_errors": load_errors,
        }

    def _baseline_assets(self, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        """Load the nearest prior verified formal run for drift checks."""
        catalog_path = self.data_center_root / "catalog.sqlite3"
        try:
            with sqlite3.connect(catalog_path) as connection:
                row = connection.execute(
                    """SELECT run_id FROM research_runs
                       WHERE verified=1 AND run_kind='formal_close_plan' AND run_id<>?
                         AND research_date<=?
                       ORDER BY research_date DESC,created_at DESC LIMIT 1""",
                    (manifest.get("run_id"), manifest.get("research_date")),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            loaded = self._load_run(str(row[0]))
        except (DataIntelligenceError, KeyError):
            return None
        loaded["assets"]["_run_id"] = str(row[0])
        return loaded["assets"]

    def _confined(self, path: Path) -> Path:
        """Reject manifest and asset paths that escape the Data Center root."""
        root = os.path.abspath(self.data_center_root)
        candidate = os.path.abspath(path)
        try:
            common = os.path.commonpath([root, candidate])
        except ValueError as exc:
            raise DataIntelligenceError("Data Center路径越界") from exc
        if os.path.normcase(common) != os.path.normcase(root):
            raise DataIntelligenceError("Data Center路径越界")
        return Path(candidate)
