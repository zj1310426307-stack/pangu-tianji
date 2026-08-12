"""Conservative filesystem/SQLite readers for existing engineering evidence."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


class LocalEvidenceAdapter:
    """Read evidence references only; never call a workflow or mutable business service."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def collect(self) -> dict[str, Any]:
        """Collect bounded availability/status evidence from existing stores."""
        return {
            "data": self._data_center(),
            "data_intelligence": self._data_intelligence(),
            "quant_lab": self._quant_lab(),
            "ai": self._ai(),
            "backups": self._backups(),
        }

    def _data_center(self) -> dict[str, Any]:
        center = self.root / "output" / "data_center"
        latest = center / "latest.json"
        payload: dict[str, Any] = {
            "latest_exists": latest.is_file(),
            "latest_mtime": None,
            "latest_run_id": None,
            "latest_run_kind": None,
            "verified_run_count": 0,
        }
        if latest.is_file():
            payload["latest_mtime"] = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat()
            try:
                pointer = json.loads(latest.read_text(encoding="utf-8"))
                payload["latest_run_id"] = pointer.get("run_id")
                payload["latest_run_kind"] = pointer.get("run_kind")
            except (OSError, json.JSONDecodeError):
                payload["latest_invalid"] = True
        catalog = center / "catalog.sqlite3"
        if catalog.is_file():
            try:
                with closing(sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True, timeout=3)) as connection:
                    payload["verified_run_count"] = int(
                        connection.execute("SELECT COUNT(*) FROM research_runs WHERE verified=1").fetchone()[0]
                    )
            except sqlite3.Error:
                payload["catalog_read_error"] = True
        return payload

    def _data_intelligence(self) -> dict[str, Any]:
        """Read the latest persisted Data Intelligence assessment and incidents."""
        path = self.root / "output" / "data_intelligence.db"
        result: dict[str, Any] = {
            "available": False,
            "latest_status": None,
            "latest_score": None,
            "latest_run_id": None,
            "latest_created_time": None,
            "open_warning_count": 0,
            "open_error_count": 0,
            "open_blocked_count": 0,
        }
        if not path.is_file():
            return result
        try:
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)) as connection:
                row = connection.execute(
                    """SELECT run_id,status,score,created_time FROM data_health
                       ORDER BY created_time DESC LIMIT 1"""
                ).fetchone()
                if row:
                    result.update(
                        {
                            "available": True,
                            "latest_run_id": row[0],
                            "latest_status": row[1],
                            "latest_score": float(row[2]),
                            "latest_created_time": row[3],
                        }
                    )
                for level, count in connection.execute(
                    """SELECT level,COUNT(*) FROM data_incidents
                       WHERE status IN ('OPEN','ACKNOWLEDGED') GROUP BY level"""
                ).fetchall():
                    key = f"open_{str(level).lower()}_count"
                    if key in result:
                        result[key] = int(count)
        except sqlite3.Error:
            result["audit_read_error"] = True
        return result

    def _quant_lab(self) -> dict[str, Any]:
        root = self.root / "output" / "quant_lab"
        artifacts = list(root.rglob("*.json")) if root.exists() else []
        invalid = 0
        for path in artifacts[:500]:
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                invalid += 1
        return {"artifact_count": len(artifacts), "invalid_json_artifact_count": invalid}

    def _ai(self) -> dict[str, Any]:
        paths = [self.root / "output" / "ai_copilot.db", self.root / "output" / "quant_ai.db"]
        result = {
            "explicit_call_count": 0,
            "failed_call_count": 0,
            "trailing_failure_count": 0,
            "latest_status": None,
            "latest_completed_time": None,
        }
        for path in paths:
            if not path.is_file():
                continue
            try:
                with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "ai_tasks" in tables:
                        result["explicit_call_count"] += int(connection.execute("SELECT COUNT(*) FROM ai_tasks").fetchone()[0])
                        columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_tasks)")}
                        if "status" in columns:
                            result["failed_call_count"] += int(connection.execute("SELECT COUNT(*) FROM ai_tasks WHERE status IN ('failed','error','rejected')").fetchone()[0])
                            rows = connection.execute(
                                """SELECT status,completed_time FROM ai_tasks
                                   WHERE status IN ('succeeded','failed')
                                   ORDER BY created_time DESC LIMIT 20"""
                            ).fetchall()
                            if rows and result["latest_status"] is None:
                                result["latest_status"] = str(rows[0][0])
                                result["latest_completed_time"] = rows[0][1]
                                trailing = 0
                                for status, _ in rows:
                                    if status != "failed":
                                        break
                                    trailing += 1
                                result["trailing_failure_count"] = trailing
            except sqlite3.Error:
                result["audit_read_error"] = True
        return result

    def _backups(self) -> dict[str, Any]:
        root = self.root / "output" / "backups"
        manifests = sorted(root.rglob("manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True) if root.exists() else []
        return {"count": len(manifests), "latest_at": datetime.fromtimestamp(manifests[0].stat().st_mtime, timezone.utc).isoformat() if manifests else None}
