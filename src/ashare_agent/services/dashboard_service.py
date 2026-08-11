from pathlib import Path
from typing import Any
import json
import os
import yaml

from ..config import load_config
from ..core.contracts import CompletedRunSnapshot
from ..repositories.run_repository import RunRepository
from .model_service import ModelService
from .run_service import RunService


class DashboardService:
    """Compose read-only dashboard data without owning strategy or broker logic."""

    def __init__(
        self,
        project_root: Path,
        run_service: RunService,
        model_service: ModelService,
        repository: RunRepository,
    ) -> None:
        self.project_root = project_root.resolve()
        self.run_service = run_service
        self.model_service = model_service
        self.repository = repository

    def _config(self):
        """Reload the safe configuration so unsafe edits fail closed."""
        try:
            return load_config(self.project_root / "config" / "settings.yaml")
        except RuntimeError:
            raise
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise RuntimeError(f"配置无法通过安全校验：{exc}") from exc

    def status(self) -> dict[str, Any]:
        """Return independent runtime, safety, model, and latest-run states."""
        config = self._config()
        raw = config.raw
        latest = self.repository.latest_completed()
        return {
            "runtime": self.run_service.status(),
            "safety": {
                "safe": True,
                "mode": raw["environment"]["mode"],
                "broker": "mock",
                "live_trading_enabled": raw["environment"]["live_trading_enabled"],
                "manual_confirmation": raw["environment"][
                    "require_manual_confirmation"
                ],
                "kill_switch": self.run_service.kill_switch,
                "model_in_execution": False,
                "data_source": raw["data"]["provider"],
            },
            "data": self.data_status(raw),
            "model": self.model_service.status(),
            "latest_backtest": latest,
        }

    def strategy_config(self) -> dict[str, Any]:
        """Expose a read-only, non-secret strategy/configuration summary."""
        raw = self._config().raw
        return {
            "initial_cash": raw["account"]["initial_cash"],
            "symbols": raw["universe"]["symbols"],
            "strategy": raw["strategy"],
            "risk": raw["risk"],
            "execution": raw["execution"],
            "costs": raw["costs"],
            "data": {
                "provider": raw["data"]["provider"],
                "base_url": raw["data"]["base_url"],
                "api_key_env": raw["data"]["api_key_env"],
                "start_date": raw["data"]["start_date"],
                "end_date": raw["data"]["end_date"],
                "interval": raw["data"]["interval"],
                "adjust": raw["data"]["adjust"],
                "minimum_rows": raw["data"]["minimum_rows"],
                "allow_cached_on_error": raw["data"]["allow_cached_on_error"],
            },
        }

    def data_status(self, raw: dict[str, Any] | None = None) -> dict[str, Any]:
        """Report historical-data readiness without implying real-time quotes."""
        config = raw or self._config().raw
        manifest_path = self.project_root / "data" / "data_manifest.json"
        manifest: dict[str, Any] | None = None
        if manifest_path.exists():
            try:
                candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
                if candidate.get("provider") == config["data"]["provider"]:
                    manifest = candidate
            except (OSError, json.JSONDecodeError):
                manifest = None
        credential_configured = bool(
            os.getenv(config["data"]["api_key_env"], "").strip()
        )
        if manifest:
            state = "cache_ready"
            message = "同花顺历史日线缓存已就绪"
        elif credential_configured:
            state = "download_required"
            message = "API Key已配置，首次回测将下载同花顺历史日线"
        else:
            state = "credential_required"
            message = "请先在服务端设置THS_FINANCE_API_KEY，再运行首次回测"
        return {
            "provider": config["data"]["provider"],
            "kind": "historical_daily",
            "state": state,
            "network_required": config["data"]["provider"] == "ths_finance",
            "real_time": False,
            "credential_configured": credential_configured,
            "cache_used": manifest.get("cache_used") if manifest else None,
            "fetched_at": manifest.get("fetched_at") if manifest else None,
            "completed_through": manifest.get("completed_through") if manifest else None,
            "message": message,
        }

    def completed_snapshot(self, run_id: str) -> CompletedRunSnapshot:
        """Build the only object a model may receive from a completed run."""
        record = self.repository.get_run(run_id)
        if record.get("state") != "succeeded":
            raise ValueError("只能解释已完成的回测")
        config = self.repository.read_settings_snapshot(run_id)
        return CompletedRunSnapshot(
            run_id=run_id,
            completed_at=record["completed_at"],
            data_source=record["data_source"],
            metrics=record["metrics"],
            strategy_summary={
                "symbols": config["universe"]["symbols"],
                "strategy": config["strategy"],
                "risk": config["risk"],
                "model_in_execution": False,
            },
        )
