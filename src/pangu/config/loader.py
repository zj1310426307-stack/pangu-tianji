"""Load, validate and fingerprint layered Pangu configuration.

Environment overrides use ``PANGU__SECTION__KEY`` and YAML scalar parsing, for
example ``PANGU__ENGINEERING__LOG_LEVEL=WARNING``.  Secrets are never returned
by the public snapshot and must remain in dedicated environment variables.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from pangu.core.exceptions import ConfigurationError
from pangu.version.schema_versions import CONFIG_SCHEMA_VERSION


_LAYER_FILES = ("base.yaml", "strategy.yaml", "risk.yaml", "ai.yaml", "scheduler.yaml", "observability.yaml")
_SECRET_TOKENS = ("secret", "password", "api_key", "token", "credential")


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), Mapping):
            target[key] = _deep_merge(dict(target[key]), value)
        else:
            target[key] = deepcopy(value)
    return target


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"缺少配置层：{path.name}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"无法读取配置层 {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"配置层 {path.name} 的根节点必须是对象")
    return value


def _apply_environment(raw: dict[str, Any], environ: Mapping[str, str]) -> list[str]:
    applied: list[str] = []
    for name, text in sorted(environ.items()):
        if not name.startswith("PANGU__"):
            continue
        keys = [part.strip().lower() for part in name[len("PANGU__") :].split("__") if part.strip()]
        if len(keys) < 2 or any(any(token in key for token in _SECRET_TOKENS) for key in keys):
            raise ConfigurationError(f"禁止通过配置快照覆盖敏感项：{name}")
        cursor: dict[str, Any] = raw
        for key in keys[:-1]:
            child = cursor.setdefault(key, {})
            if not isinstance(child, dict):
                raise ConfigurationError(f"环境变量路径与配置结构冲突：{name}")
            cursor = child
        try:
            cursor[keys[-1]] = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"环境变量值无法解析：{name}") from exc
        applied.append(name)
    return applied


def _redact(value: Any, key: str = "") -> Any:
    if any(token in key.lower() for token in _SECRET_TOKENS) and not key.lower().endswith("_env"):
        return "***REDACTED***"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _validate(raw: dict[str, Any], root: Path) -> None:
    if raw.get("config_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigurationError("config_version 与配置中心 Schema 不一致")
    system = raw.get("system")
    engineering = raw.get("engineering")
    security = raw.get("security")
    if not all(isinstance(item, dict) for item in (system, engineering, security)):
        raise ConfigurationError("system、engineering、security 必须为对象")
    if system.get("environment") not in {"development", "production"}:
        raise ConfigurationError("system.environment 只能为 development 或 production")
    if system.get("timezone") != "Asia/Shanghai":
        raise ConfigurationError("当前系统时区必须为 Asia/Shanghai")
    if system.get("live_trading_enabled") is not False:
        raise ConfigurationError("工程配置中心禁止启用实盘")
    if security.get("allow_live_broker") is not False:
        raise ConfigurationError("工程配置中心禁止开放真实券商")
    weights = engineering.get("health_weights")
    if not isinstance(weights, dict) or set(weights) != {"data", "database", "ai", "strategy"}:
        raise ConfigurationError("health_weights 必须完整包含 data/database/ai/strategy")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
        raise ConfigurationError("health_weights 必须为非负数")
    if abs(sum(float(value) for value in weights.values()) - 100.0) > 1e-9:
        raise ConfigurationError("health_weights 总和必须等于100")
    for key in ("event_db", "backup_root"):
        candidate = Path(str(engineering.get(key, "")))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ConfigurationError(f"engineering.{key} 必须是项目内相对路径")
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ConfigurationError(f"engineering.{key} 不得离开项目目录")
    observability = raw.get("observability")
    if not isinstance(observability, dict):
        raise ConfigurationError("observability 必须为对象")
    database = Path(str(observability.get("database", "")))
    if database.is_absolute() or ".." in database.parts or not (root / database).resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("observability.database 必须是项目内相对路径")
    if observability.get("schema_version") != "pangu-observability-db-v1.0.0":
        raise ConfigurationError("observability.schema_version 不受支持")
    retention = observability.get("retention")
    if not isinstance(retention, dict) or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
        for value in retention.values()
    ):
        raise ConfigurationError("observability.retention 必须全部为正数")


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable public configuration snapshot with provenance and hash."""

    config_version: str
    environment: str
    configuration: Mapping[str, Any]
    source_files: tuple[str, ...]
    environment_overrides: tuple[str, ...]
    config_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe defensive copy."""
        return {
            "config_version": self.config_version,
            "environment": self.environment,
            "configuration": deepcopy(dict(self.configuration)),
            "source_files": list(self.source_files),
            "environment_overrides": list(self.environment_overrides),
            "config_hash": self.config_hash,
        }


class ConfigCenter:
    """Resolve layered configuration without mutating legacy business settings."""

    def __init__(
        self,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
        *,
        config_root: Path | None = None,
    ):
        # Keep an ASCII junction intact on Windows for legacy SQLite builds.
        self.project_root = Path(os.path.abspath(project_root))
        self.config_root = Path(os.path.abspath(config_root or self.project_root / "config"))
        self.environ = dict(os.environ if environ is None else environ)

    def load(self, environment: str | None = None) -> ConfigSnapshot:
        """Load the base layers, environment layer and safe overrides."""
        selected = str(environment or self.environ.get("PANGU_ENV") or "development").lower()
        if selected not in {"development", "production"}:
            raise ConfigurationError("PANGU_ENV 只能为 development 或 production")
        files = [*(_LAYER_FILES), f"{selected}.yaml"]
        raw: dict[str, Any] = {}
        for name in files:
            _deep_merge(raw, _read_yaml(self.config_root / name))
        applied = _apply_environment(raw, self.environ)
        _validate(raw, self.project_root)
        public = _redact(raw)
        canonical = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return ConfigSnapshot(
            config_version=CONFIG_SCHEMA_VERSION,
            environment=selected,
            configuration=MappingProxyType(deepcopy(public)),
            source_files=tuple(files),
            environment_overrides=tuple(applied),
            config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


def load_config_snapshot(project_root: Path, environment: str | None = None) -> ConfigSnapshot:
    """Convenience entry point for API, health and backup services."""
    return ConfigCenter(project_root).load(environment)
