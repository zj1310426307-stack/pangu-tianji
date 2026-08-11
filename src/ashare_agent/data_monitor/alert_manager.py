from __future__ import annotations

from typing import Any

from .store import DataIntelligenceStore


class DataAlertManager:
    """Expose incident workflow without granting a route around the data gate."""

    def __init__(self, store: DataIntelligenceStore) -> None:
        self.store = store

    def list(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Return incidents and deterministic counts for the dashboard."""
        items = self.store.incidents(status=status, limit=limit)
        counts = {
            "total": len(items),
            "open": sum(item["status"] == "OPEN" for item in items),
            "acknowledged": sum(item["status"] == "ACKNOWLEDGED" for item in items),
            "warning": sum(item["level"] == "WARNING" for item in items),
            "error": sum(item["level"] == "ERROR" for item in items),
            "blocked": sum(item["level"] == "BLOCKED" for item in items),
        }
        return {"items": items, "counts": counts}

    def acknowledge(self, incident_id: str) -> dict[str, Any]:
        """Mark an incident seen; acknowledgement never changes publish_allowed."""
        return self.store.acknowledge(incident_id)
