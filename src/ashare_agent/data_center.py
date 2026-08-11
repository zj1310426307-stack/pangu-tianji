from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

import numpy as np
import pandas as pd

from .data_center_models import (
    DATA_MODEL_VERSION,
    FEATURE_STORE,
    FINANCIAL_POINT_IN_TIME,
    MARKET_DATA_HISTORY,
    MARKET_DATA_SNAPSHOT,
    RESEARCH_SNAPSHOT,
    SECURITY_MASTER,
    DataModelError,
)
from .research_pipeline import ResearchDataSource, ResearchPipeline, ResearchPipelineResult


DATA_CENTER_SCHEMA_VERSION = "2.0.0"
DATA_CENTER_LAYERS = ("raw", "clean", "point_in_time", "features", "snapshots")
SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?:api[_-]?key|token|password|secret|authorization|credential)",
    re.IGNORECASE,
)
ALLOWED_METADATA_FIELDS = {
    "observed_at",
    "quote_timestamp_ms",
    "report_period",
    "provider",
    "preview",
    "source",
}


class DataCenterError(RuntimeError):
    """Reject a research publication when its immutable evidence cannot be saved."""


@dataclass(frozen=True)
class DataCenterRunRecord:
    """Identify one immutable research run and its data contract."""

    run_id: str
    data_version: str
    strategy_version: str
    research_date: str
    manifest_path: Path
    asset_count: int
    verified: bool = False


@dataclass(frozen=True)
class DataCenterPipelineRun:
    """Return the authoritative Data Center replay and its immutable run identity."""

    result: ResearchPipelineResult
    record: DataCenterRunRecord


class DataCenterResearchDataSource:
    """Read one persisted raw evidence set through the ResearchDataSource protocol."""

    def __init__(
        self,
        universe: pd.DataFrame,
        snapshot: pd.DataFrame,
        history: pd.DataFrame,
        fundamentals: pd.DataFrame,
        *,
        run_id: str,
    ) -> None:
        self.universe_frame = universe.copy()
        self.snapshot_frame = snapshot.copy()
        self.history_frame = history.copy()
        self.fundamentals_frame = fundamentals.copy()
        self.run_id = run_id
        if "date" in self.history_frame:
            self.history_frame["date"] = pd.to_datetime(
                self.history_frame["date"], errors="coerce"
            ).dt.normalize()
        if "available_at" in self.fundamentals_frame:
            self.fundamentals_frame["available_at"] = pd.to_datetime(
                self.fundamentals_frame["available_at"], errors="coerce"
            ).dt.normalize()

    def security_universe(self, as_of: pd.Timestamp) -> pd.DataFrame:
        """Return the persisted security master visible to this research run."""
        return self.universe_frame.copy()

    def data_snapshot(self, as_of: pd.Timestamp, universe: pd.DataFrame) -> pd.DataFrame:
        """Return the persisted market snapshot instead of calling a provider."""
        return self.snapshot_frame.copy()

    def price_history(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return only persisted completed bars requested by the shared Pipeline."""
        return self.history_frame[
            self.history_frame["symbol"].astype(str).isin(symbols)
            & (self.history_frame["date"] <= pd.Timestamp(as_of).normalize())
        ].copy()

    def fundamentals(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return the latest persisted financial row visible at the research date."""
        visible = self.fundamentals_frame[
            self.fundamentals_frame["symbol"].astype(str).isin(symbols)
            & (
                self.fundamentals_frame["available_at"]
                <= pd.Timestamp(as_of).normalize()
            )
        ].copy()
        if visible.empty:
            return visible
        return visible.sort_values("available_at").groupby("symbol", as_index=False).tail(1)


def _json_safe(value: Any) -> Any:
    """Convert pandas and NumPy values into deterministic JSON-compatible values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _canonical_frame(frame: pd.DataFrame) -> bytes:
    """Serialize one tabular asset with stable columns, row order and date formatting."""
    normalized = frame.copy()
    normalized.columns = [str(column) for column in normalized.columns]
    columns = sorted(normalized.columns)
    normalized = normalized.reindex(columns=columns)
    sort_columns = [
        column
        for column in ("date", "available_at", "symbol", "thscode", "ticker", "rank")
        if column in normalized.columns
    ]
    if sort_columns:
        try:
            normalized = normalized.sort_values(sort_columns, kind="mergesort", na_position="last")
        except TypeError:
            normalized = normalized.assign(
                **{column: normalized[column].astype(str) for column in sort_columns}
            ).sort_values(sort_columns, kind="mergesort", na_position="last")
    records = [_json_safe(row) for row in normalized.to_dict("records")]
    return json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_json(payload: Any) -> bytes:
    """Serialize non-tabular evidence without platform-dependent formatting."""
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_strategy_version(value: str) -> str:
    """Keep the full strategy version visible while making it path-safe."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    if not safe:
        raise DataCenterError("strategy_version无法用于Data Center run_id")
    return safe


def _validate_evidence_fields(frames: dict[str, pd.DataFrame]) -> None:
    """Fail closed if provider data could accidentally persist a credential field."""
    sensitive: list[str] = []
    for asset_name, frame in frames.items():
        for column in frame.columns:
            if SENSITIVE_FIELD_PATTERN.search(str(column)):
                sensitive.append(f"{asset_name}.{column}")
    if sensitive:
        raise DataCenterError(
            "Data Center rejected sensitive evidence fields: " + ", ".join(sensitive)
        )


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Persist only an explicit provenance allowlist and reject credential-like keys."""
    if not metadata:
        return {}
    sensitive = [key for key in metadata if SENSITIVE_FIELD_PATTERN.search(str(key))]
    if sensitive:
        raise DataCenterError(
            "Data Center metadata must not contain credentials: " + ", ".join(sensitive)
        )
    return {
        str(key): _json_safe(value)
        for key, value in metadata.items()
        if str(key) in ALLOWED_METADATA_FIELDS
    }


class DataCenter:
    """Persist immutable research inputs, snapshots, features and factor evidence.

    Data Center owns only data lineage and run identity. It never reads an
    account, changes a score, constructs an order or participates in trading.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        for layer in DATA_CENTER_LAYERS:
            (self.root / layer).mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.sqlite3"
        self._initialize_catalog()

    def execute_pipeline(
        self,
        pipeline: ResearchPipeline,
        as_of: pd.Timestamp | str,
        source: ResearchDataSource,
        *,
        run_kind: str,
        metadata: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    ) -> DataCenterPipelineRun:
        """Ingest provider data, persist it, then return an authoritative DC replay.

        The first pass discovers the exact dynamic universe required by the shared
        Pipeline. The persisted raw assets are then read back through
        ``DataCenterResearchDataSource`` and replayed. Production callers receive
        only the replayed result, so live research and backtests share the same
        point-in-time read boundary.
        """
        ingested = pipeline.run(as_of, source)
        metadata_payload = metadata() if callable(metadata) else metadata
        record = self.record_research(
            ingested,
            run_kind=run_kind,
            metadata=metadata_payload,
        )
        replayed = pipeline.run(as_of, self.load_research_source(record.run_id))
        self._verify_replay(ingested, replayed)
        self._mark_verified(record.run_id)
        return DataCenterPipelineRun(result=replayed, record=replace(record, verified=True))

    def record_research(
        self,
        result: ResearchPipelineResult,
        *,
        run_kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> DataCenterRunRecord:
        """Write one complete research evidence package and return its unique identity."""
        evidence_frames = {
            "raw_security_universe": result.raw_universe,
            "raw_market_snapshot": result.raw_snapshot,
            "raw_price_history": result.raw_history,
            "raw_fundamentals": result.raw_fundamentals,
            "security_master": result.universe,
            "market_data": result.raw_history,
            "security_universe_pit": result.universe,
            "market_data_pit": result.snapshot,
            "financial_point_in_time": result.raw_fundamentals,
            "feature_store": result.features,
            "factor_store": result.factor_results,
            "final_ranking": result.ranked,
        }
        _validate_evidence_fields(evidence_frames)
        safe_metadata = _safe_metadata(metadata)
        as_of = pd.Timestamp(result.as_of).normalize()
        try:
            model_checks = {
                "security_master": SECURITY_MASTER.validate(result.universe, as_of=as_of),
                "market_data_snapshot": MARKET_DATA_SNAPSHOT.validate(
                    result.snapshot, as_of=as_of
                ),
                "market_data_history": MARKET_DATA_HISTORY.validate(
                    result.raw_history, as_of=as_of
                ),
                "financial_point_in_time": FINANCIAL_POINT_IN_TIME.validate(
                    result.raw_fundamentals, as_of=as_of
                ),
                "feature_store": FEATURE_STORE.validate(result.features, as_of=as_of),
            }
        except DataModelError as exc:
            raise DataCenterError(str(exc)) from exc
        assets: dict[str, tuple[bytes, int, str, str, str]] = {
            "raw_security_universe": (
                _canonical_frame(result.raw_universe), len(result.raw_universe),
                "raw", "security_master", "json-records+gzip"
            ),
            "raw_market_snapshot": (
                _canonical_frame(result.raw_snapshot), len(result.raw_snapshot),
                "raw", "market_data_snapshot", "json-records+gzip"
            ),
            "raw_price_history": (
                _canonical_frame(result.raw_history), len(result.raw_history),
                "raw", "market_data_history", "json-records+gzip"
            ),
            "raw_fundamentals": (
                _canonical_frame(result.raw_fundamentals), len(result.raw_fundamentals),
                "raw", "financial_point_in_time", "json-records+gzip"
            ),
            "security_master": (
                _canonical_frame(result.universe), len(result.universe),
                "clean", "security_master", "json-records+gzip"
            ),
            "market_data": (
                _canonical_frame(result.raw_history), len(result.raw_history),
                "clean", "market_data", "json-records+gzip"
            ),
            "security_universe_pit": (
                _canonical_frame(result.universe), len(result.universe),
                "point_in_time", "security_master", "json-records+gzip"
            ),
            "market_data_pit": (
                _canonical_frame(result.snapshot), len(result.snapshot),
                "point_in_time", "market_data", "json-records+gzip"
            ),
            "financial_point_in_time": (
                _canonical_frame(result.raw_fundamentals), len(result.raw_fundamentals),
                "point_in_time", "financial_point_in_time", "json-records+gzip"
            ),
            "feature_store": (
                _canonical_frame(result.features), len(result.features),
                "features", "feature_store", "json-records+gzip"
            ),
            "factor_store": (
                _canonical_frame(result.factor_results), len(result.factor_results),
                "features", "feature_store", "json-records+gzip"
            ),
            "final_ranking": (
                _canonical_frame(result.ranked), len(result.ranked),
                "snapshots", "research_snapshot", "json-records+gzip"
            ),
            "portfolio_weights": (
                _canonical_json({
                    "targets": result.targets,
                    "target_weights": result.target_weights,
                    "rejections": result.portfolio_rejections,
                }),
                len(result.targets),
                "snapshots", "research_snapshot", "json+gzip"
            ),
        }
        raw_hashes = {
            name: hashlib.sha256(content).hexdigest()
            for name, (content, _, layer, _, _) in assets.items()
            if layer == "raw"
        }
        data_contract = {
            "research_date": result.as_of.date().isoformat(),
            "raw_hashes": raw_hashes,
        }
        data_version = hashlib.sha256(_canonical_json(data_contract)).hexdigest()[:16]
        safe_strategy = _safe_strategy_version(result.strategy_version)
        run_id = (
            f"{result.as_of.date().isoformat()}_{safe_strategy}_"
            f"dv-{data_version}_{uuid4().hex[:8]}"
        )
        research_date = result.as_of.date().isoformat()
        manifest_path = self.root / "snapshots" / research_date / run_id / "manifest.json"
        research_snapshot = {
            "run_id": run_id,
            "research_date": research_date,
            "strategy_version": result.strategy_version,
            "factor_version": result.factor_model_version,
            "factor_contract_hash": result.factor_contract_hash,
            "data_version": data_version,
            "ranking_count": int(len(result.ranked)),
            "targets": list(result.targets),
            "target_weights": dict(result.target_weights),
        }
        try:
            model_checks["research_snapshot"] = RESEARCH_SNAPSHOT.validate(research_snapshot)
        except DataModelError as exc:
            raise DataCenterError(str(exc)) from exc
        universe_symbols = set(result.universe["symbol"].astype(str))
        feature_symbols = set(result.features["symbol"].astype(str))
        ranking_symbols = set(result.ranked["symbol"].astype(str))
        referential_integrity = (
            feature_symbols.issubset(universe_symbols)
            and ranking_symbols.issubset(feature_symbols)
        )
        if not referential_integrity:
            raise DataCenterError("Data Center模型引用完整性检查失败")
        try:
            manifest_assets: dict[str, dict[str, Any]] = {}
            for name, (content, rows, layer, model_name, format_name) in assets.items():
                content_hash = hashlib.sha256(content).hexdigest()
                relative_path = Path(layer) / model_name / research_date / f"{content_hash}.json.gz"
                target = self.root / relative_path
                compressed = gzip.compress(content, compresslevel=6, mtime=0)
                if target.exists():
                    existing = gzip.decompress(target.read_bytes())
                    if hashlib.sha256(existing).hexdigest() != content_hash:
                        raise DataCenterError(f"Data Center内容寻址资产损坏：{relative_path}")
                else:
                    self._atomic_write_bytes(target, compressed)
                manifest_assets[name] = {
                    "layer": layer,
                    "model": model_name,
                    "model_version": DATA_MODEL_VERSION,
                    "path": relative_path.as_posix(),
                    "format": format_name,
                    "sha256": content_hash,
                    "rows": int(rows),
                    "uncompressed_bytes": len(content),
                    "compressed_bytes": len(compressed),
                }
            manifest = {
                "schema_version": DATA_CENTER_SCHEMA_VERSION,
                "run_id": run_id,
                "run_kind": run_kind,
                "research_date": result.as_of.date().isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "strategy_version": result.strategy_version,
                "factor_version": result.factor_model_version,
                "factor_model_version": result.factor_model_version,
                "factor_contract_hash": result.factor_contract_hash,
                "data_version": data_version,
                "data_model_version": DATA_MODEL_VERSION,
                "data_source": safe_metadata.get("provider") or safe_metadata.get("source") or "unknown",
                "data_time": safe_metadata.get("observed_at") or research_date,
                "stage_counts": result.stage_counts,
                "data_quality": result.data_quality,
                "quality_checks": {
                    "models": model_checks,
                    "referential_integrity": True,
                    "sensitive_fields_absent": True,
                    "content_hashes_present": True,
                },
                "metadata": safe_metadata,
                "assets": manifest_assets,
            }
            self._atomic_write_bytes(
                manifest_path,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
            latest = {
                "run_id": run_id,
                "data_version": data_version,
                "strategy_version": result.strategy_version,
                "research_date": result.as_of.date().isoformat(),
                "manifest": manifest_path.relative_to(self.root).as_posix(),
            }
            self.root.mkdir(parents=True, exist_ok=True)
            self._atomic_write_bytes(
                self.root / "latest.json",
                json.dumps(latest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
            self._atomic_write_bytes(
                self.root / "snapshots" / "latest.json",
                json.dumps(latest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
            self._catalog_record(manifest)
        except DataCenterError:
            raise
        except Exception as exc:
            raise DataCenterError(f"Data Center保存研究证据失败：{str(exc)[:300]}") from exc
        return DataCenterRunRecord(
            run_id=run_id,
            data_version=data_version,
            strategy_version=result.strategy_version,
            research_date=result.as_of.date().isoformat(),
            manifest_path=manifest_path,
            asset_count=len(assets),
        )

    def manifest(self, run_id: str) -> dict[str, Any]:
        """Read one run manifest through the metadata catalog with path confinement."""
        with sqlite3.connect(self.catalog_path) as connection:
            row = connection.execute(
                "SELECT manifest_path FROM research_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            raise DataCenterError(f"Data Center不存在run_id：{run_id}")
        path = (self.root / str(row[0])).resolve()
        if self.root.resolve() not in path.parents:
            raise DataCenterError("Data Center manifest路径越界")
        return json.loads(path.read_text(encoding="utf-8"))

    def read_asset(self, run_id: str, asset_name: str) -> Any:
        """Read and checksum one immutable asset referenced by a run manifest."""
        manifest = self.manifest(run_id)
        asset = manifest.get("assets", {}).get(asset_name)
        if not asset:
            raise DataCenterError(f"run_id缺少数据资产：{asset_name}")
        path = (self.root / str(asset["path"])).resolve()
        if self.root.resolve() not in path.parents:
            raise DataCenterError("Data Center asset路径越界")
        content = gzip.decompress(path.read_bytes())
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise DataCenterError(f"Data Center资产校验失败：{asset_name}")
        return json.loads(content.decode("utf-8"))

    def load_research_source(self, run_id: str) -> DataCenterResearchDataSource:
        """Build the only production replay adapter from persisted raw evidence."""
        return DataCenterResearchDataSource(
            pd.DataFrame(self.read_asset(run_id, "raw_security_universe")),
            pd.DataFrame(self.read_asset(run_id, "raw_market_snapshot")),
            pd.DataFrame(self.read_asset(run_id, "raw_price_history")),
            pd.DataFrame(self.read_asset(run_id, "raw_fundamentals")),
            run_id=run_id,
        )

    @staticmethod
    def _verify_replay(
        ingested: ResearchPipelineResult,
        replayed: ResearchPipelineResult,
    ) -> None:
        """Fail closed when persisted evidence cannot reproduce ranking and weights."""
        ranking_equal = _canonical_frame(ingested.ranked) == _canonical_frame(replayed.ranked)
        portfolio_equal = _canonical_json({
            "targets": ingested.targets,
            "target_weights": ingested.target_weights,
            "rejections": ingested.portfolio_rejections,
        }) == _canonical_json({
            "targets": replayed.targets,
            "target_weights": replayed.target_weights,
            "rejections": replayed.portfolio_rejections,
        })
        if not ranking_equal or not portfolio_equal:
            raise DataCenterError("Data Center权威重放与采集结果不一致，禁止发布研究结果")

    def _initialize_catalog(self) -> None:
        """Create the local metadata catalog; bulk investment data remains in layers."""
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.catalog_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    run_kind TEXT NOT NULL,
                    research_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    factor_version TEXT NOT NULL,
                    factor_contract_hash TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    data_time TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS data_assets (
                    run_id TEXT NOT NULL,
                    asset_name TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    asset_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    PRIMARY KEY (run_id, asset_name),
                    FOREIGN KEY (run_id) REFERENCES research_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS data_models (
                    model_name TEXT PRIMARY KEY,
                    model_version TEXT NOT NULL,
                    logical_layer TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT OR REPLACE INTO data_models VALUES (?, ?, ?)",
                [
                    ("Security Master", DATA_MODEL_VERSION, "clean/point_in_time"),
                    ("Market Data", DATA_MODEL_VERSION, "raw/clean/point_in_time"),
                    ("Financial Point-In-Time", DATA_MODEL_VERSION, "point_in_time"),
                    ("Feature Store", DATA_MODEL_VERSION, "features"),
                    ("Research Snapshot", DATA_MODEL_VERSION, "snapshots"),
                ],
            )

    def _catalog_record(self, manifest: dict[str, Any]) -> None:
        """Index one immutable manifest and all content-addressed assets atomically."""
        manifest_path = Path("snapshots") / manifest["research_date"] / manifest["run_id"] / "manifest.json"
        with sqlite3.connect(self.catalog_path) as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    run_id, run_kind, research_date, created_at, strategy_version,
                    factor_version, factor_contract_hash, data_version, data_source,
                    data_time, manifest_path, verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    manifest["run_id"], manifest["run_kind"], manifest["research_date"],
                    manifest["created_at"], manifest["strategy_version"],
                    manifest["factor_version"], manifest["factor_contract_hash"],
                    manifest["data_version"], manifest["data_source"],
                    manifest["data_time"], manifest_path.as_posix(),
                ),
            )
            connection.executemany(
                """
                INSERT INTO data_assets (
                    run_id, asset_name, layer, model_name, model_version,
                    asset_path, sha256, row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        manifest["run_id"], name, asset["layer"], asset["model"],
                        asset["model_version"], asset["path"], asset["sha256"],
                        int(asset["rows"]),
                    )
                    for name, asset in manifest["assets"].items()
                ],
            )

    def _mark_verified(self, run_id: str) -> None:
        """Mark the catalog row only after deterministic Data Center replay succeeds."""
        with sqlite3.connect(self.catalog_path) as connection:
            connection.execute(
                "UPDATE research_runs SET verified = 1 WHERE run_id = ?", (run_id,)
            )

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        """Atomically publish one asset and tolerate short Windows scanner locks."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(content)
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.02 * (attempt + 1))
