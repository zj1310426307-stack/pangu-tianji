from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..quant_lab.contracts import canonical_bytes, sha256_json, utc_now
from .contracts import EVOLUTION_SCHEMA_VERSION, EvolutionArtifactError


_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


class EvolutionArtifactStore:
    """Seal one health report and manifest in an immutable project-local directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        strategy_id: str,
        evaluation_id: str,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist canonical bytes idempotently and reject changed retries."""
        directory = self._directory(strategy_id, evaluation_id)
        directory.mkdir(parents=True, exist_ok=True)
        report_bytes = canonical_bytes(report)
        report_hash = sha256_json(report)
        report_path = directory / "strategy_health_report.json"
        if report_path.exists() and report_path.read_bytes() != report_bytes:
            raise EvolutionArtifactError("同一evaluation_id的Strategy Health Report已被改变")
        if not report_path.exists():
            report_path.write_bytes(report_bytes)
        manifest_content = {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "strategy_id": strategy_id,
            "evaluation_id": evaluation_id,
            "created_at": report.get("generated_at") or utc_now(),
            "artifacts": [{
                "name": report_path.name,
                "sha256": report_hash,
                "size": len(report_bytes),
            }],
        }
        manifest = {**manifest_content, "manifest_hash": sha256_json(manifest_content)}
        manifest_path = directory / "manifest.json"
        manifest_bytes = canonical_bytes(manifest)
        if manifest_path.exists() and manifest_path.read_bytes() != manifest_bytes:
            raise EvolutionArtifactError("同一evaluation_id的manifest已被改变")
        if not manifest_path.exists():
            manifest_path.write_bytes(manifest_bytes)
        completed = directory / ".completed"
        if not completed.exists():
            completed.write_text(manifest["manifest_hash"], encoding="ascii")
        return manifest

    def read(self, strategy_id: str, evaluation_id: str) -> dict[str, Any]:
        """Verify report bytes, manifest hash and completion marker before returning."""
        directory = self._directory(strategy_id, evaluation_id)
        manifest_path = directory / "manifest.json"
        report_path = directory / "strategy_health_report.json"
        completed = directory / ".completed"
        if not manifest_path.is_file() or not report_path.is_file() or not completed.is_file():
            raise EvolutionArtifactError("策略进化artifact不完整")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_content = {
            key: manifest.get(key)
            for key in ("schema_version", "strategy_id", "evaluation_id", "created_at", "artifacts")
        }
        if sha256_json(manifest_content) != manifest.get("manifest_hash"):
            raise EvolutionArtifactError("策略进化manifest哈希不一致")
        if completed.read_text(encoding="ascii").strip() != manifest.get("manifest_hash"):
            raise EvolutionArtifactError("策略进化完成标记不一致")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        artifact = (manifest.get("artifacts") or [{}])[0]
        if sha256_json(report) != artifact.get("sha256"):
            raise EvolutionArtifactError("Strategy Health Report哈希不一致")
        return {"report": report, "manifest": manifest}

    def _directory(self, strategy_id: str, evaluation_id: str) -> Path:
        """Confine strategy and evaluation identities to safe directory components."""
        if not _SAFE.fullmatch(strategy_id) or not _SAFE.fullmatch(evaluation_id):
            raise EvolutionArtifactError("策略或评估ID不能用于artifact路径")
        return self.root / strategy_id / evaluation_id

