"""Create immutable, checksummed backups of local investment evidence."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pangu.core.exceptions import BackupError
from pangu.observability import EngineeringEventStore, PanguLogger
from pangu.observability.event_store import utc_now
from pangu.version import get_version_manifest
from pangu.version.schema_versions import BACKUP_MANIFEST_VERSION


_SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupService:
    """Back up databases, Data Center, configuration and Quant Lab artifacts only."""

    def __init__(
        self,
        project_root: Path,
        backup_root: Path,
        store: EngineeringEventStore,
        logger: PanguLogger,
    ) -> None:
        self.root = Path(os.path.abspath(project_root))
        configured = Path(backup_root)
        self.backup_root = configured if configured.is_absolute() else self.root / configured
        self.backup_root = Path(os.path.abspath(self.backup_root))
        if not self.backup_root.resolve().is_relative_to((self.root / "output").resolve()):
            raise BackupError("备份目录必须位于项目 output 下")
        self.store = store
        self.logger = logger

    def create(self, *, trace_id: str | None = None) -> dict[str, Any]:
        """Create and verify one non-overwriting backup generation."""
        started_at = utc_now()
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        backup_id = f"backup-{day}-{uuid4().hex[:12]}"
        destination = self.backup_root / day / backup_id
        temporary = destination.with_name(f".{backup_id}.partial")
        trace = trace_id or str(uuid4())
        self.backup_root.mkdir(parents=True, exist_ok=True)
        if destination.exists() or temporary.exists():
            raise BackupError("备份目标已经存在，拒绝覆盖")
        self.store.start_backup(backup_id, started_at, destination.relative_to(self.root).as_posix())
        try:
            temporary.mkdir(parents=True)
            records: list[dict[str, Any]] = []
            for source, relative in self._sources():
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    self._backup_sqlite(source, target)
                else:
                    shutil.copy2(source, target)
                records.append(
                    {
                        "source": source.relative_to(self.root).as_posix(),
                        "path": relative.as_posix(),
                        "size": target.stat().st_size,
                        "sha256": _sha256(target),
                    }
                )
            records.sort(key=lambda item: item["path"])
            manifest = {
                "schema_version": BACKUP_MANIFEST_VERSION,
                "backup_id": backup_id,
                "created_at": started_at,
                "version_manifest": get_version_manifest().to_dict(),
                "file_count": len(records),
                "byte_count": sum(item["size"] for item in records),
                "files": records,
                "can_trade": False,
                "can_create_orders": False,
            }
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            manifest_hash = _sha256(manifest_path)
            self._verify(temporary, records)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(temporary, destination)
            except PermissionError:
                # Some Windows endpoint filters deny directory rename even when
                # all contained files are closed.  Copy to a unique destination,
                # verify again, then remove only the confined partial directory.
                shutil.copytree(temporary, destination, copy_function=shutil.copy2)
                self._verify(destination, records)
                if not temporary.resolve().is_relative_to(self.backup_root.resolve()):
                    raise BackupError("临时备份目录越界，拒绝清理")
                shutil.rmtree(temporary)
            self.store.finish_backup(
                backup_id,
                status="SUCCEEDED",
                manifest_hash=manifest_hash,
                file_count=manifest["file_count"],
                byte_count=manifest["byte_count"],
            )
            self.logger.event(
                "INFO", "backup", "backup_completed", "工程备份已完成并校验",
                trace_id=trace,
                extra={"backup_id": backup_id, "file_count": len(records), "manifest_hash": manifest_hash},
            )
            return {
                **manifest,
                "status": "SUCCEEDED",
                "backup_path": destination.relative_to(self.root).as_posix(),
                "manifest_hash": manifest_hash,
            }
        except Exception as exc:  # Boundary: all filesystem/SQLite failures receive a terminal audit state.
            audit_error: str | None = None
            try:
                self.store.finish_backup(backup_id, status="FAILED", error_message=f"{type(exc).__name__}: {exc}")
            except Exception as audit_exc:  # Preserve the original failure while recording audit degradation.
                audit_error = type(audit_exc).__name__
            self.logger.event(
                "ERROR", "backup", "backup_failed", "工程备份失败",
                trace_id=trace,
                extra={"backup_id": backup_id, "error_type": type(exc).__name__, "audit_error": audit_error},
            )
            raise BackupError(f"备份失败：{exc}") from exc

    def _sources(self) -> list[tuple[Path, Path]]:
        selected: dict[Path, Path] = {}
        for source in (self.root / "config").glob("*.yaml"):
            if source.name.lower() not in _SECRET_NAMES:
                selected[source] = Path("config") / source.name
        output = self.root / "output"
        for source in output.rglob("*") if output.exists() else ():
            if not source.is_file() or source.is_relative_to(self.backup_root):
                continue
            relative = source.relative_to(output)
            if source.name.lower() in _SECRET_NAMES or source.suffix.lower() in {".log", ".jsonl"}:
                continue
            if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                selected[source] = Path("database") / relative
            elif relative.parts and relative.parts[0] == "data_center":
                selected[source] = Path("data_center") / Path(*relative.parts[1:])
            elif relative.parts and relative.parts[0] == "quant_lab":
                selected[source] = Path("quant_lab") / Path(*relative.parts[1:])
        return sorted(selected.items(), key=lambda item: item[1].as_posix())

    @staticmethod
    def _backup_sqlite(source: Path, target: Path) -> None:
        try:
            with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10)) as origin:
                with closing(sqlite3.connect(target)) as copy:
                    origin.backup(copy)
                    result = str(copy.execute("PRAGMA quick_check").fetchone()[0])
                    if result != "ok":
                        raise BackupError(f"SQLite 备份校验失败：{source.name}")
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite 备份失败：{source.name}: {exc}") from exc

    @staticmethod
    def _verify(root: Path, records: list[dict[str, Any]]) -> None:
        for record in records:
            path = root / record["path"]
            if not path.is_file() or path.stat().st_size != record["size"] or _sha256(path) != record["sha256"]:
                raise BackupError(f"备份文件校验失败：{record['path']}")
