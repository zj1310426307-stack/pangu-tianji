"""Human-controlled engineering incident lifecycle and immutable timeline."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from .contracts import AlertSeverity, INCIDENT_TRANSITIONS, IncidentStatus, canonical_hash
from .store import ObservabilityStore


class IncidentService:
    """Maintain incident evidence without remediating or changing investment state."""

    def __init__(self, store: ObservabilityStore) -> None:
        self.store = store

    def create(
        self,
        *,
        title: str,
        severity: str,
        impact: str,
        root_cause: str = "",
        links: list[dict[str, str]] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Create one explicit incident and append its opening timeline event."""
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > 160:
            raise ValueError("Incident 标题长度无效")
        incident_id = f"incident-{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        timeline = [{"at": now, "actor": actor[:80], "event": "CREATED", "note": "用户显式创建 Incident"}]
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO incidents(incident_id,title,severity,status,opened_at,updated_at,root_cause,impact,timeline_json,resolution_note,
                version,can_auto_remediate,can_trade,can_create_orders) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    incident_id, normalized_title, AlertSeverity(severity).value, IncidentStatus.OPEN.value, now, now,
                    root_cause[:2000], impact[:2000], json.dumps(timeline, ensure_ascii=False), "", 1, 0, 0, 0,
                ),
            )
            for link in links or []:
                connection.execute(
                    "INSERT OR IGNORE INTO incident_links(incident_id,link_type,link_id,created_at) VALUES(?,?,?,?)",
                    (incident_id, link["link_type"], link["link_id"][:160], now),
                )
            connection.commit()
        return self.get(incident_id)

    def transition(
        self,
        incident_id: str,
        *,
        target: str,
        expected_version: int,
        actor: str,
        note: str,
        root_cause: str | None = None,
        resolution_note: str | None = None,
    ) -> dict[str, Any]:
        """Compare-and-set one legal user transition and retain the full timeline."""
        destination = IncidentStatus(target)
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
            if not row:
                raise ValueError("Incident 不存在")
            origin = IncidentStatus(row["status"])
            if int(row["version"]) != int(expected_version):
                raise ValueError("Incident 已被其他操作更新")
            if destination not in INCIDENT_TRANSITIONS[origin]:
                raise ValueError(f"Incident 不可从 {origin.value} 转为 {destination.value}")
            now = datetime.now(timezone.utc).isoformat()
            timeline = json.loads(row["timeline_json"])
            timeline.append({"at": now, "actor": actor[:80], "event": f"{origin.value}->{destination.value}", "note": note[:800]})
            resolved_at = now if destination in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED} else row["resolved_at"]
            cursor = connection.execute(
                """UPDATE incidents SET status=?,updated_at=?,resolved_at=?,root_cause=?,timeline_json=?,resolution_note=?,version=version+1
                WHERE incident_id=? AND version=?""",
                (
                    destination.value, now, resolved_at, root_cause[:2000] if root_cause is not None else row["root_cause"],
                    json.dumps(timeline, ensure_ascii=False), resolution_note[:2000] if resolution_note is not None else row["resolution_note"],
                    incident_id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Incident 并发更新冲突")
            connection.commit()
        return self.get(incident_id)

    def get(self, incident_id: str) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
        if not row:
            raise ValueError("Incident 不存在")
        row["timeline"] = json.loads(row.pop("timeline_json"))
        row["links"] = self.store.all("SELECT link_type,link_id,created_at FROM incident_links WHERE incident_id=? ORDER BY created_at", (incident_id,))
        row["evidence_hash"] = canonical_hash({key: value for key, value in row.items() if key not in {"timeline", "links"}})
        row.update({"can_auto_remediate": False, "can_trade": False, "can_create_orders": False})
        return row

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            ids = self.store.all("SELECT incident_id FROM incidents WHERE status=? ORDER BY updated_at DESC LIMIT ?", (IncidentStatus(status).value, max(1, min(limit, 500))))
        else:
            ids = self.store.all("SELECT incident_id FROM incidents ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),))
        return [self.get(row["incident_id"]) for row in ids]
