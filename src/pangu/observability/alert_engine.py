"""Deduplicated alert lifecycle with acknowledgement, suppression and recovery."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from .alert_rules import AlertRule, DEFAULT_ALERT_RULES
from .contracts import AlertSeverity, AlertStatus, canonical_hash
from .store import ObservabilityStore


class AlertEngine:
    """Maintain alert evidence only; never invoke a remediation callback."""

    def __init__(self, store: ObservabilityStore, *, cooldown_seconds: int = 900, consecutive_triggers: int = 1) -> None:
        self.store = store
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.consecutive_triggers = max(1, int(consecutive_triggers))
        self.rules = {rule.rule_id: rule for rule in DEFAULT_ALERT_RULES}

    def observe(
        self,
        rule_id: str,
        active: bool,
        *,
        root_key: str,
        evidence: dict[str, Any],
        trace_id: str | None = None,
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Open/deduplicate/update or resolve one root-cause alert."""
        rule = self.rules.get(rule_id)
        if not rule:
            raise ValueError("告警规则不存在")
        current = now or datetime.now(timezone.utc)
        fingerprint = canonical_hash({"rule_id": rule_id, "root_key": root_key})
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM alerts WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not active:
                connection.execute("DELETE FROM alert_trigger_state WHERE fingerprint=?", (fingerprint,))
                if not row or row["status"] == AlertStatus.RESOLVED.value:
                    connection.commit()
                    return dict(row) if row else None
                connection.execute(
                    "UPDATE alerts SET status='RESOLVED',resolved_at=?,last_seen_at=? WHERE alert_id=?",
                    (current.isoformat(), current.isoformat(), row["alert_id"]),
                )
                self._event(connection, row["alert_id"], "RESOLVED", "system", "触发条件已恢复")
                connection.commit()
                return self.get(row["alert_id"])
            encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            evidence_hash = canonical_hash(evidence)
            trigger = connection.execute("SELECT consecutive_count FROM alert_trigger_state WHERE fingerprint=?", (fingerprint,)).fetchone()
            trigger_count = int(trigger["consecutive_count"] if trigger else 0) + 1
            connection.execute(
                """INSERT INTO alert_trigger_state(fingerprint,rule_id,consecutive_count,last_seen_at,evidence_hash)
                VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET consecutive_count=excluded.consecutive_count,
                last_seen_at=excluded.last_seen_at,evidence_hash=excluded.evidence_hash""",
                (fingerprint, rule_id, trigger_count, current.isoformat(), evidence_hash),
            )
            if row is None and trigger_count < self.consecutive_triggers:
                connection.commit()
                return None
            if row:
                count = int(row["consecutive_count"]) + 1
                status = row["status"]
                suppressed_until = row["suppressed_until"]
                if status == AlertStatus.RESOLVED.value:
                    status, count = AlertStatus.OPEN.value, 1
                if suppressed_until and current >= datetime.fromisoformat(suppressed_until) and status == AlertStatus.SUPPRESSED.value:
                    status = AlertStatus.OPEN.value
                connection.execute(
                    """UPDATE alerts SET status=?,last_seen_at=?,resolved_at=NULL,trace_id=?,job_id=?,consecutive_count=?,
                    evidence_json=?,evidence_hash=?,message=?,recommended_manual_action=? WHERE alert_id=?""",
                    (status, current.isoformat(), trace_id, job_id, count, encoded, evidence_hash, rule.message, rule.recommended_manual_action, row["alert_id"]),
                )
                connection.commit()
                return self.get(row["alert_id"])
            alert_id = f"alert-{uuid4().hex}"
            cooldown = (current + timedelta(seconds=self.cooldown_seconds)).isoformat()
            connection.execute(
                """INSERT INTO alerts(alert_id,rule_id,fingerprint,severity,status,opened_at,last_seen_at,trace_id,job_id,consecutive_count,cooldown_until,
                evidence_json,evidence_hash,message,recommended_manual_action,can_auto_remediate,can_trade,can_create_orders)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    alert_id, rule_id, fingerprint, rule.severity.value, AlertStatus.OPEN.value, current.isoformat(), current.isoformat(),
                    trace_id, job_id, 1, cooldown, encoded, evidence_hash, rule.message, rule.recommended_manual_action, 0, 0, 0,
                ),
            )
            self._event(connection, alert_id, "OPENED", "system", rule.message)
            connection.commit()
        return self.get(alert_id)

    def acknowledge(self, alert_id: str, *, actor: str, note: str = "") -> dict[str, Any]:
        """Record user acknowledgement without altering the underlying condition."""
        return self._manual_transition(alert_id, AlertStatus.ACKNOWLEDGED, actor, note or "用户已确认")

    def suppress(self, alert_id: str, *, actor: str, until: datetime, note: str = "") -> dict[str, Any]:
        """Temporarily suppress presentation while keeping the alert history."""
        if until <= datetime.now(timezone.utc):
            raise ValueError("抑制结束时间必须在未来")
        result = self._manual_transition(alert_id, AlertStatus.SUPPRESSED, actor, note or "用户临时抑制")
        self.store.execute("UPDATE alerts SET suppressed_until=? WHERE alert_id=?", (until.isoformat(), alert_id))
        return self.get(alert_id)

    def _manual_transition(self, alert_id: str, status: AlertStatus, actor: str, note: str) -> dict[str, Any]:
        """Apply an allowed user-only alert state mutation with audit evidence."""
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
            if not row:
                raise ValueError("告警不存在")
            if row["status"] == AlertStatus.RESOLVED.value:
                raise ValueError("已恢复告警不可确认或抑制")
            connection.execute("UPDATE alerts SET status=? WHERE alert_id=?", (status.value, alert_id))
            self._event(connection, alert_id, status.value, actor[:80], note[:500])
            connection.commit()
        return self.get(alert_id)

    @staticmethod
    def _event(connection, alert_id: str, event_type: str, actor: str, note: str) -> None:
        occurred = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO alert_events(event_id,alert_id,event_type,occurred_at,actor,note,evidence_hash) VALUES(?,?,?,?,?,?,?)",
            (f"alert-event-{uuid4().hex}", alert_id, event_type, occurred, actor, note, canonical_hash({"alert_id": alert_id, "event_type": event_type, "occurred_at": occurred, "actor": actor, "note": note})),
        )

    def get(self, alert_id: str) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
        if not row:
            raise ValueError("告警不存在")
        row["evidence"] = json.loads(row.pop("evidence_json"))
        row["events"] = self.store.all("SELECT * FROM alert_events WHERE alert_id=? ORDER BY occurred_at", (alert_id,))
        row.update({"can_auto_remediate": False, "can_trade": False, "can_create_orders": False})
        return row

    def list(self, *, status: str | None = None, severity: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses, parameters = [], []
        if status:
            clauses.append("status=?")
            parameters.append(AlertStatus(status).value)
        if severity:
            clauses.append("severity=?")
            parameters.append(AlertSeverity(severity).value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(limit, 500)))
        return [self.get(row["alert_id"]) for row in self.store.all(f"SELECT alert_id FROM alerts {where} ORDER BY last_seen_at DESC LIMIT ?", parameters)]
