from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
from typing import Any, Mapping
from uuid import uuid4

from .contracts import canonical_bytes, sha256_json
from .exceptions import ArtifactIntegrityError, ImmutableExperimentError


class ArtifactStore:
    """Write canonical, content-hashed experiment evidence into isolated run folders."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, experiment_id: str, run_id: str) -> Path:
        """Keep reruns separate while preserving the requested experiment hierarchy."""
        return self.root / experiment_id / run_id

    def write_json(self, experiment_id: str, run_id: str, name: str, payload: Any) -> dict[str, Any]:
        """Write one canonical JSON artifact exactly once."""
        if not name.endswith(".json") or "/" in name or "\\" in name:
            raise ArtifactIntegrityError("artifact名称必须是单层.json文件")
        directory = self.run_dir(experiment_id, run_id)
        if (directory / ".completed").exists():
            raise ImmutableExperimentError("已完成实验目录不可修改")
        content = canonical_bytes(payload)
        target = directory / name
        if target.exists():
            if target.read_bytes() != content:
                raise ImmutableExperimentError(f"artifact已存在且内容不同：{name}")
        else:
            self._atomic_write(target, content)
        return self._metadata(experiment_id, run_id, target)

    def finalize(self, experiment_id: str, run_id: str, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        """Publish artifact_manifest last and seal the directory after verification."""
        directory = self.run_dir(experiment_id, run_id)
        if (directory / ".completed").exists():
            raise ImmutableExperimentError("实验artifact已经完成")
        manifest = {
            "schema_version": "pangu-quant-lab-artifacts-v1.0.0",
            "experiment_id": experiment_id,
            "run_id": run_id,
            "artifacts": sorted(artifacts, key=lambda item: item["name"]),
        }
        manifest["manifest_hash"] = sha256_json(manifest)
        manifest_item = self.write_json(experiment_id, run_id, "artifact_manifest.json", manifest)
        self.verify(experiment_id, run_id, {**manifest, "artifacts": artifacts})
        marker = canonical_bytes({"manifest_hash": manifest["manifest_hash"]})
        self._atomic_write(directory / ".completed", marker)
        return {**manifest, "manifest_artifact": manifest_item}

    def verify(self, experiment_id: str, run_id: str, manifest: Mapping[str, Any]) -> None:
        """Detect missing, path-escaped or tampered experiment artifacts."""
        directory = self.run_dir(experiment_id, run_id).resolve()
        for item in manifest.get("artifacts", []):
            path = (self.root / item["path"]).resolve()
            if directory != path.parent or not path.is_file():
                raise ArtifactIntegrityError(f"artifact缺失或路径越界：{item['name']}")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ArtifactIntegrityError(f"artifact哈希不一致：{item['name']}")
            if len(content) != int(item["size_bytes"]):
                raise ArtifactIntegrityError(f"artifact大小不一致：{item['name']}")

    def _metadata(self, experiment_id: str, run_id: str, target: Path) -> dict[str, Any]:
        """Return the registry-safe relative identity of one artifact."""
        content = target.read_bytes()
        return {
            "name": target.name,
            "path": target.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": "application/json",
        }

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        """Publish a file atomically without replacing an existing artifact."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(content)
        try:
            if target.exists():
                raise ImmutableExperimentError(f"artifact不可覆盖：{target.name}")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
