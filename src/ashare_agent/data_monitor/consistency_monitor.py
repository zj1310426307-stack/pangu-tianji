from __future__ import annotations

from typing import Any, Mapping

from .contracts import component, issue


class DataConsistencyMonitor:
    """Check catalog verification, hashes and manifest referential integrity."""

    def evaluate(
        self,
        manifest: Mapping[str, Any],
        *,
        catalog_verified: bool,
        manifest_hash_valid: bool,
        catalog_assets_valid: bool,
        asset_hashes_valid: bool,
    ) -> dict[str, Any]:
        """Return a consistency score that fails closed on any immutable-evidence mismatch."""
        quality_checks = dict(manifest.get("quality_checks") or {})
        referential = quality_checks.get("referential_integrity") is True
        content_hashes = quality_checks.get("content_hashes_present") is True
        sensitive_absent = quality_checks.get("sensitive_fields_absent") is True
        model_checks = dict(quality_checks.get("models") or {})
        models_valid = bool(model_checks) and all(
            isinstance(value, Mapping) and value.get("valid") is True
            for value in model_checks.values()
        )
        checks = {
            "catalog_verified": bool(catalog_verified),
            "manifest_hash_valid": bool(manifest_hash_valid),
            "catalog_assets_valid": bool(catalog_assets_valid),
            "asset_hashes_valid": bool(asset_hashes_valid),
            "referential_integrity": referential,
            "content_hashes_present": content_hashes,
            "sensitive_fields_absent": sensitive_absent,
            "models_valid": models_valid,
        }
        problems: list[dict[str, Any]] = []
        labels = {
            "catalog_verified": "Data Center权威重放尚未验证",
            "manifest_hash_valid": "manifest身份校验失败",
            "catalog_assets_valid": "manifest资产索引与Data Center catalog不一致",
            "asset_hashes_valid": "数据资产内容哈希失败",
            "referential_integrity": "数据模型引用完整性失败",
            "content_hashes_present": "manifest缺少内容哈希声明",
            "sensitive_fields_absent": "敏感字段安全检查未通过",
            "models_valid": "统一数据模型校验未全部通过",
        }
        for name, valid in checks.items():
            if not valid:
                problems.append(issue(
                    "consistency", name.upper(), "BLOCKED", labels[name], blocking=True,
                ))
        score = sum(1 for value in checks.values() if value) / len(checks) * 100
        return component("consistency", score, checks, problems)
