from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .contracts import (
    DATA_INTELLIGENCE_SCHEMA_VERSION,
    DATA_INTELLIGENCE_VERSION,
    content_hash,
    stable_id,
    utc_now,
)


class DataIntelligenceStore:
    """Persist monitoring metadata without editing immutable Data Center evidence."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived connection with foreign keys and bounded waiting."""
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        """Create additive monitoring tables with database-enforced zero capabilities."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_health (
                    health_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    monitor_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    research_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('NORMAL','WARNING','ERROR','BLOCKED')),
                    score REAL NOT NULL CHECK(score >= 0 AND score <= 100),
                    components_json TEXT NOT NULL,
                    issues_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    blocking INTEGER NOT NULL CHECK(blocking IN (0,1)),
                    publish_allowed INTEGER NOT NULL CHECK(publish_allowed IN (0,1)),
                    preview INTEGER NOT NULL CHECK(preview IN (0,1)),
                    created_time TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_historical_data INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_historical_data=0),
                    can_bypass_gate INTEGER NOT NULL DEFAULT 0 CHECK(can_bypass_gate=0),
                    UNIQUE(run_id, monitor_version)
                );
                CREATE TABLE IF NOT EXISTS data_incidents (
                    incident_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    code TEXT NOT NULL,
                    level TEXT NOT NULL CHECK(level IN ('WARNING','ERROR','BLOCKED')),
                    description TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
                    created_time TEXT NOT NULL,
                    acknowledged_time TEXT,
                    resolved_time TEXT,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_bypass_gate INTEGER NOT NULL DEFAULT 0 CHECK(can_bypass_gate=0),
                    UNIQUE(run_id, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS data_catalog (
                    catalog_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    monitor_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    source TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    coverage REAL NOT NULL CHECK(coverage >= 0 AND coverage <= 1),
                    quality_score REAL NOT NULL CHECK(quality_score >= 0 AND quality_score <= 100),
                    status TEXT NOT NULL CHECK(status IN ('NORMAL','WARNING','ERROR','BLOCKED')),
                    health_id TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_modify_historical_data INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_historical_data=0),
                    UNIQUE(run_id, monitor_version),
                    FOREIGN KEY(health_id) REFERENCES data_health(health_id)
                );
                CREATE TABLE IF NOT EXISTS data_lineage (
                    edge_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_modify_historical_data INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_historical_data=0),
                    UNIQUE(run_id, from_id, to_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_data_health_created ON data_health(created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_data_incidents_status ON data_incidents(status, created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_data_catalog_created ON data_catalog(created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_data_lineage_run ON data_lineage(run_id, from_id, to_id);
                """
            )

    def save_assessment(
        self,
        assessment: Mapping[str, Any],
        catalog: Mapping[str, Any],
        edges: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist one immutable health snapshot, incidents, catalog row and lineage graph."""
        run_id = str(assessment["run_id"])
        health_id = stable_id("health", run_id, DATA_INTELLIGENCE_VERSION)
        components = dict(assessment["components"])
        issues = list(assessment.get("issues") or [])
        evidence_hash = content_hash({
            "run_id": run_id, "components": components, "issues": issues,
            "manifest_hash": catalog["manifest_hash"],
        })
        created = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO data_health(
                    health_id,run_id,monitor_version,data_version,research_date,status,
                    score,components_json,issues_json,evidence_hash,blocking,publish_allowed,
                    preview,created_time
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    health_id, run_id, DATA_INTELLIGENCE_VERSION,
                    assessment["data_version"], assessment["research_date"],
                    assessment["status"], float(assessment["score"]),
                    self._json(components), self._json(issues), evidence_hash,
                    int(bool(assessment["blocking"])), int(bool(assessment["publish_allowed"])),
                    int(bool(assessment["preview"])), created,
                ),
            )
            for item in issues:
                fingerprint = content_hash({
                    "category": item["category"], "code": item["code"],
                    "message": item["message"], "details": item.get("details") or {},
                })
                incident_id = stable_id("incident", run_id, fingerprint)
                connection.execute(
                    """INSERT OR IGNORE INTO data_incidents(
                        incident_id,run_id,category,code,level,description,evidence_json,
                        fingerprint,status,created_time
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        incident_id, run_id, item["category"], item["code"],
                        item["level"], item["message"], self._json(item.get("details") or {}),
                        fingerprint, "OPEN", created,
                    ),
                )
            catalog_id = stable_id("catalog", run_id, DATA_INTELLIGENCE_VERSION)
            connection.execute(
                """INSERT OR IGNORE INTO data_catalog(
                    catalog_id,run_id,monitor_version,data_version,source,manifest_hash,
                    coverage,quality_score,status,health_id,created_time
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    catalog_id, run_id, DATA_INTELLIGENCE_VERSION, assessment["data_version"],
                    catalog["source"], catalog["manifest_hash"], float(catalog["coverage"]),
                    float(assessment["score"]), assessment["status"], health_id, created,
                ),
            )
            for edge in edges:
                edge_hash = content_hash(dict(edge))
                edge_id = stable_id(
                    "lineage", run_id, edge["from_id"], edge["to_id"], edge["relation"]
                )
                connection.execute(
                    """INSERT OR IGNORE INTO data_lineage(
                        edge_id,run_id,from_id,to_id,relation,evidence_hash,created_time
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        edge_id, run_id, edge["from_id"], edge["to_id"],
                        edge["relation"], edge_hash, created,
                    ),
                )
        return self.health(run_id) or {}

    def health(self, run_id: str) -> dict[str, Any] | None:
        """Read the latest monitor-version health snapshot for one run."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM data_health WHERE run_id=?
                   ORDER BY created_time DESC LIMIT 1""", (run_id,),
            ).fetchone()
        return self._health_row(row) if row else None

    def latest_health(self) -> dict[str, Any] | None:
        """Read the newest persisted health snapshot without evaluating data on GET."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_health ORDER BY created_time DESC LIMIT 1"
            ).fetchone()
        return self._health_row(row) if row else None

    def incidents(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List immutable incidents with their current acknowledgement status."""
        query = "SELECT * FROM data_incidents"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_time DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._incident_row(row) for row in rows]

    def acknowledge(self, incident_id: str) -> dict[str, Any]:
        """Acknowledge one event without resolving or deleting the underlying evidence."""
        now = utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE data_incidents SET status='ACKNOWLEDGED',acknowledged_time=?
                   WHERE incident_id=? AND status='OPEN'""", (now, incident_id),
            )
            row = connection.execute(
                "SELECT * FROM data_incidents WHERE incident_id=?", (incident_id,),
            ).fetchone()
        if row is None:
            raise KeyError(incident_id)
        if result.rowcount not in {0, 1}:
            raise RuntimeError("数据事件确认状态异常")
        return self._incident_row(row)

    def catalog(self, limit: int = 100) -> list[dict[str, Any]]:
        """List monitored immutable data versions and their health scores."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM data_catalog ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def lineage(self, run_id: str) -> list[dict[str, Any]]:
        """List persisted lineage edges for one Data Center run."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM data_lineage WHERE run_id=?
                   ORDER BY from_id,to_id,relation""", (run_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _json(value: Any) -> str:
        """Serialize a bounded monitoring payload in canonical form."""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        """Convert one SQLite row into a public dictionary."""
        return {key: row[key] for key in row.keys()}

    def _health_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """Decode one data-health row and restore boolean safety fields."""
        payload = self._row(row)
        payload["components"] = json.loads(payload.pop("components_json"))
        payload["issues"] = json.loads(payload.pop("issues_json"))
        for name in (
            "blocking", "publish_allowed", "preview", "can_trade", "can_create_orders",
            "can_modify_historical_data", "can_bypass_gate",
        ):
            payload[name] = bool(payload[name])
        return payload

    def _incident_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """Decode one incident while retaining its immutable fingerprint."""
        payload = self._row(row)
        payload["evidence"] = json.loads(payload.pop("evidence_json"))
        for name in ("can_trade", "can_create_orders", "can_bypass_gate"):
            payload[name] = bool(payload[name])
        return payload

    def schema(self) -> dict[str, str]:
        """Expose version identifiers for diagnostics and reports."""
        return {
            "service_version": DATA_INTELLIGENCE_VERSION,
            "schema_version": DATA_INTELLIGENCE_SCHEMA_VERSION,
        }
