"""Read-mostly facade for the engineering dashboard and local operator actions."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any, Callable

from pangu.backup import BackupService
from pangu.config import ConfigCenter
from pangu.health import EngineeringHealthService
from pangu.observability import EngineeringEventStore, PanguLogger, configure_structured_logging
from pangu.observability.service import ObservabilityService
from pangu.version import ENGINEERING_VERSION, get_version_manifest


class EngineeringService:
    """Compose configuration, audit, health and backup without domain imports."""

    def __init__(
        self,
        project_root: Path,
        model_status_provider: Callable[[], dict[str, Any]] | None = None,
        *,
        environment: str | None = None,
    ) -> None:
        # Keep an ASCII junction intact on Windows for legacy SQLite builds.
        self.root = Path(os.path.abspath(project_root))
        local_config = self.root / "config"
        packaged_config = Path(__file__).absolute().parents[3] / "config"
        config_root = local_config if (local_config / "base.yaml").is_file() else packaged_config
        self.config = ConfigCenter(self.root, config_root=config_root).load(environment)
        engineering = self.config.configuration["engineering"]
        event_db = self.root / str(engineering["event_db"])
        self.store = EngineeringEventStore(event_db)
        base_logger = configure_structured_logging(
            self.root / "output" / "logs" / "pangu-engineering.jsonl",
            str(engineering["log_level"]),
        )
        self.logger = PanguLogger(base_logger, self.store)
        self.health = EngineeringHealthService(
            self.root,
            self.config,
            self.store,
            self.logger,
            model_status_provider,
        )
        self.backup = BackupService(
            self.root,
            self.root / str(engineering["backup_root"]),
            self.store,
            self.logger,
        )
        self.observability = ObservabilityService(
            self.root,
            dict(self.config.configuration["observability"]),
            self.config.config_hash,
            engineering_provider=self.dashboard,
        )

    @staticmethod
    def safety() -> dict[str, bool]:
        """Expose fixed non-investment capabilities at every engineering boundary."""
        return {
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_access_broker_credentials": False,
        }

    def dashboard(self) -> dict[str, Any]:
        """Read current engineering state without starting checks or backups."""
        recent_events = self.store.list_events(20)
        recent_backups = self.store.list_backups(20)
        latest_health = self.health.latest()
        return {
            "service_version": ENGINEERING_VERSION,
            "versions": get_version_manifest().to_dict(),
            "configuration": self.config.to_dict(),
            "health": latest_health or {
                "status": "NOT_EVALUATED",
                "score": None,
                "components": {},
                "checked_at": None,
            },
            "backups": recent_backups,
            "events": recent_events,
            "counts": {
                "event_count": len(recent_events),
                "backup_count": len(recent_backups),
                "successful_backup_count": sum(item.get("status") == "SUCCEEDED" for item in recent_backups),
            },
            "safety": self.safety(),
            **self.safety(),
        }

    def run_health(self) -> dict[str, Any]:
        """Evaluate engineering dependencies; never call investment workflows."""
        payload = self.health.evaluate(persist=True)
        payload["safety"] = self.safety()
        return payload

    def create_backup(self) -> dict[str, Any]:
        """Create a local evidence backup; never mutate the source artifacts."""
        payload = self.backup.create()
        payload["safety"] = self.safety()
        payload.update(self.safety())
        return payload

    def list_events(self, limit: int = 50) -> dict[str, Any]:
        """Return recent structured events."""
        return {"items": self.store.list_events(limit), "safety": self.safety(), **self.safety()}

    def list_backups(self, limit: int = 30) -> dict[str, Any]:
        """Return backup audit records without reading backup contents."""
        return {"items": self.store.list_backups(limit), "safety": self.safety(), **self.safety()}
