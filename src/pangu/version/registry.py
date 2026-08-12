"""Immutable aggregate version manifest used by APIs, logs and backups."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from .factor_versions import FACTOR_RESEARCH_VERSION, PRODUCTION_FACTOR_VERSION
from .model_versions import (
    AI_COPILOT_VERSION,
    AI_QUANT_RESEARCH_VERSION,
    MODEL_INTERFACE_VERSION,
)
from .schema_versions import (
    BACKUP_MANIFEST_VERSION,
    CONFIG_SCHEMA_VERSION,
    DATA_CENTER_SCHEMA_VERSION,
    DATA_MODEL_VERSION,
    ENGINEERING_DB_SCHEMA_VERSION,
    OBSERVABILITY_DB_SCHEMA_VERSION,
)
from .strategy_versions import (
    PRODUCTION_STRATEGY_VERSION,
    STRATEGY_EVOLUTION_VERSION,
    STRATEGY_VALIDATION_VERSION,
)
from .system_version import (
    API_VERSION,
    ENGINEERING_VERSION,
    SYSTEM_VERSION,
    VERSION_REGISTRY_SCHEMA,
)


@dataclass(frozen=True)
class VersionManifest:
    """One immutable, serializable view of every engineering contract."""

    registry_schema: str
    system_version: str
    api_version: str
    engineering_version: str
    strategy_versions: dict[str, str]
    factor_versions: dict[str, str]
    schema_versions: dict[str, str]
    model_versions: dict[str, str]
    manifest_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive JSON-compatible copy."""
        return asdict(self)


def _payload() -> dict[str, Any]:
    return {
        "registry_schema": VERSION_REGISTRY_SCHEMA,
        "system_version": SYSTEM_VERSION,
        "api_version": API_VERSION,
        "engineering_version": ENGINEERING_VERSION,
        "strategy_versions": {
            "production": PRODUCTION_STRATEGY_VERSION,
            "validation": STRATEGY_VALIDATION_VERSION,
            "evolution": STRATEGY_EVOLUTION_VERSION,
        },
        "factor_versions": {
            "production": PRODUCTION_FACTOR_VERSION,
            "research": FACTOR_RESEARCH_VERSION,
        },
        "schema_versions": {
            "data_model": DATA_MODEL_VERSION,
            "data_center": DATA_CENTER_SCHEMA_VERSION,
            "engineering_db": ENGINEERING_DB_SCHEMA_VERSION,
            "observability_db": OBSERVABILITY_DB_SCHEMA_VERSION,
            "config": CONFIG_SCHEMA_VERSION,
            "backup_manifest": BACKUP_MANIFEST_VERSION,
        },
        "model_versions": {
            "provider": MODEL_INTERFACE_VERSION,
            "copilot": AI_COPILOT_VERSION,
            "quant_research": AI_QUANT_RESEARCH_VERSION,
        },
    }


def get_version_manifest() -> VersionManifest:
    """Build a deterministic version manifest and content hash."""
    payload = _payload()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return VersionManifest(**payload, manifest_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest())
