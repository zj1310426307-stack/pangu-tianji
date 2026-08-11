from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import UUID
import yaml


class RunRepository:
    """Persist append-only run metadata and immutable completed artifacts."""

    ARTIFACTS = {
        "report": "backtest_report.md",
        "metrics": "metrics.json",
        "equity": "equity_curve.csv",
        "database": "agent.db",
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_dir = self.project_root / "output"
        self.runs_dir = self.output_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> str:
        """Return a timezone-aware UTC timestamp."""
        return datetime.now(timezone.utc).isoformat()

    def _run_dir(self, run_id: str) -> Path:
        """Validate a UUID and return its confined snapshot directory."""
        canonical = str(UUID(run_id))
        return self.runs_dir / canonical

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        """Atomically write strict JSON so readers never see partial state."""
        sanitized = RunRepository._sanitize(payload)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _sanitize(value: Any) -> Any:
        """Reject non-finite API values instead of silently emitting invalid JSON."""
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("运行结果包含非有限数值")
            return value
        if isinstance(value, dict):
            return {str(key): RunRepository._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [RunRepository._sanitize(item) for item in value]
        return value

    @staticmethod
    def _sha256(path: Path) -> str:
        """Calculate a stable SHA-256 digest for reproducibility metadata."""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def create_run(self, run_id: str, started_at: str) -> dict[str, Any]:
        """Create one append-only running record before work begins."""
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        config_path = self.project_root / "config" / "settings.yaml"
        snapshot_path = run_dir / "settings.snapshot.yaml"
        shutil.copy2(config_path, snapshot_path)
        snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
        data_source = str(snapshot.get("data", {}).get("provider", "unknown"))
        record = {
            "run_id": run_id,
            "state": "running",
            "stage": "queued",
            "started_at": started_at,
            "completed_at": None,
            "data_source": data_source,
            "error_message": None,
            "metrics": None,
            "config_sha256": self._sha256(snapshot_path),
        }
        self._write_json(run_dir / "run.json", record)
        return record

    def update_stage(self, run_id: str, stage: str) -> None:
        """Update the observable stage without touching prior successful runs."""
        record = self.get_run(run_id)
        record["stage"] = stage
        record["updated_at"] = self._now()
        self._write_json(self._run_dir(run_id) / "run.json", record)

    def complete_run(self, run_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        """Copy completed artifacts and finalize reproducibility metadata."""
        run_dir = self._run_dir(run_id)
        required = [
            self.output_dir / "metrics.json",
            self.output_dir / "equity_curve.csv",
            self.output_dir / "backtest_report.md",
            self.output_dir / "agent.db",
        ]
        missing = [str(path.name) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(f"缺少运行产物：{', '.join(missing)}")
        for source in required:
            shutil.copy2(source, run_dir / source.name)
        data_dir = self.project_root / "data"
        data_files = sorted(path for path in data_dir.glob("*") if path.is_file())
        data_hashes = {path.name: self._sha256(path) for path in data_files}
        if not data_hashes:
            raise FileNotFoundError("缺少已验证的行情快照")
        market_snapshot = run_dir / "market_data"
        market_snapshot.mkdir(parents=True, exist_ok=False)
        for source in data_files:
            shutil.copy2(source, market_snapshot / source.name)
        manifest_path = data_dir / "data_manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        record = self.get_run(run_id)
        record.update(
            {
                "state": "succeeded",
                "stage": "completed",
                "completed_at": self._now(),
                "metrics": self._sanitize(metrics),
                "data_sha256": data_hashes,
                "data_source": str(manifest.get("provider") or record["data_source"]),
                "data_completed_through": manifest.get("completed_through")
                or metrics.get("data_completed_through"),
                "data_cache_used": bool(
                    manifest.get("cache_used", metrics.get("data_cache_used", False))
                ),
                "artifacts": sorted(self.ARTIFACTS.values())
                + ["settings.snapshot.yaml", "market_data/"],
            }
        )
        self._write_json(run_dir / "run.json", record)
        return record

    def settings_snapshot_path(self, run_id: str) -> Path:
        """Return the confined configuration captured before a run starts."""
        path = self._run_dir(run_id) / "settings.snapshot.yaml"
        if not path.exists():
            raise FileNotFoundError("运行配置快照不存在")
        return path

    def fail_run(self, run_id: str, message: str) -> dict[str, Any]:
        """Finalize a failed record without replacing the latest successful run."""
        record = self.get_run(run_id)
        record.update(
            {
                "state": "failed",
                "stage": "failed",
                "completed_at": self._now(),
                "error_message": message[:1000],
            }
        )
        self._write_json(self._run_dir(run_id) / "run.json", record)
        return record

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Read one run record by validated UUID."""
        path = self._run_dir(run_id) / "run.json"
        if not path.exists():
            raise FileNotFoundError("运行记录不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def _records(self) -> list[dict[str, Any]]:
        """Read valid run metadata, ignoring unrelated directories."""
        records: list[dict[str, Any]] = []
        for path in self.runs_dir.glob("*/run.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return records

    def latest_completed(self) -> dict[str, Any] | None:
        """Return the newest successful snapshot, if one exists."""
        completed = [item for item in self._records() if item.get("state") == "succeeded"]
        return max(completed, key=lambda item: item.get("completed_at") or "") if completed else None

    def recover_stale_runs(self) -> None:
        """Mark process-interrupted running records as failed on service startup."""
        for record in self._records():
            if record.get("state") == "running":
                self.fail_run(record["run_id"], "服务重启：上一次运行未正常完成")

    def read_settings_snapshot(self, run_id: str) -> dict[str, Any]:
        """Read and hash-check the exact configuration used by a completed run."""
        record = self.get_run(run_id)
        path = self._run_dir(run_id) / "settings.snapshot.yaml"
        if not path.exists():
            raise FileNotFoundError("运行配置快照不存在")
        expected = record.get("config_sha256")
        if not expected or self._sha256(path) != expected:
            raise ValueError("运行配置快照校验失败")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("运行配置快照格式无效")
        return payload

    def read_equity(self, run_id: str, limit: int = 2000) -> list[dict[str, Any]]:
        """Read a bounded equity curve from an immutable run snapshot."""
        path = self._run_dir(run_id) / "equity_curve.csv"
        if not path.exists():
            raise FileNotFoundError("净值曲线不存在")
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))[-limit:]
        return [
            {
                "date": row["date"],
                "cash": float(row["cash"]),
                "market_value": float(row["market_value"]),
                "equity": float(row["equity"]),
            }
            for row in rows
        ]

    def read_activity(
        self, run_id: str, kind: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Query a completed SQLite snapshot with one read-only connection."""
        db_path = self._run_dir(run_id) / "agent.db"
        if not db_path.exists():
            raise FileNotFoundError("运行数据库不存在")
        connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 1000")
        try:
            if kind == "orders":
                sql = """
                    SELECT trade_date, symbol, side, quantity, requested_price,
                           status, reject_reason, client_order_id
                    FROM orders ORDER BY trade_date DESC, rowid DESC LIMIT ?
                """
            elif kind == "rejections":
                sql = """
                    SELECT trade_date, symbol, side, quantity, requested_price,
                           status, reject_reason, client_order_id
                    FROM orders WHERE reject_reason IS NOT NULL
                    ORDER BY trade_date DESC, rowid DESC LIMIT ?
                """
            elif kind == "trades":
                sql = """
                    SELECT trade_date, symbol, side, quantity, fill_price, fee,
                           client_order_id
                    FROM trades ORDER BY trade_date DESC, rowid DESC LIMIT ?
                """
            elif kind == "signals":
                sql = """
                    SELECT trade_date AS signal_date, symbol, score,
                           target_weight, reason
                    FROM signals ORDER BY trade_date DESC, rowid DESC LIMIT ?
                """
            else:
                raise ValueError("不支持的活动类型")
            return [dict(row) for row in connection.execute(sql, (limit,)).fetchall()]
        finally:
            connection.close()

    def read_workbench_evidence(self, run_id: str) -> dict[str, Any]:
        """Aggregate audit evidence for the decision workbench without writes."""
        db_path = self._run_dir(run_id) / "agent.db"
        if not db_path.exists():
            raise FileNotFoundError("运行数据库不存在")
        connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            signals = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN reason IS NOT NULL AND TRIM(reason) <> '' THEN 1 ELSE 0 END) AS with_reason,
                           MAX(trade_date) AS latest_date
                    FROM signals
                    """
                ).fetchone()
            )
            orders = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN client_order_id IS NOT NULL AND TRIM(client_order_id) <> '' THEN 1 ELSE 0 END) AS with_client_id,
                           SUM(CASE WHEN reject_reason IS NOT NULL THEN 1 ELSE 0 END) AS rejected,
                           SUM(CASE WHEN status = 'UNKNOWN' THEN 1 ELSE 0 END) AS unknown,
                           MAX(trade_date) AS latest_date
                    FROM orders
                    """
                ).fetchone()
            )
            trades = dict(
                connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN client_order_id IS NOT NULL AND TRIM(client_order_id) <> '' THEN 1 ELSE 0 END) AS with_client_id,
                           SUM(CASE WHEN fee IS NOT NULL THEN 1 ELSE 0 END) AS with_fee,
                           COALESCE(SUM(fee), 0) AS total_fees,
                           MAX(trade_date) AS latest_date
                    FROM trades
                    """
                ).fetchone()
            )
            latest_rows = connection.execute(
                """
                SELECT trade_date AS signal_date, symbol, score, target_weight, reason
                FROM signals ORDER BY trade_date DESC, rowid DESC
                """
            ).fetchall()
        finally:
            connection.close()
        latest_signals: dict[str, dict[str, Any]] = {}
        for row in latest_rows:
            item = dict(row)
            latest_signals.setdefault(item["symbol"], item)
        for group in (signals, orders, trades):
            for key, value in list(group.items()):
                if value is None and key not in {"latest_date"}:
                    group[key] = 0
        return {
            "signals": signals,
            "orders": orders,
            "trades": trades,
            "latest_signals": latest_signals,
        }

    def artifact_path(self, run_id: str, artifact: str) -> Path:
        """Return one allowlisted immutable artifact path."""
        filename = self.ARTIFACTS.get(artifact)
        if filename is None:
            raise ValueError("不支持的产物类型")
        path = self._run_dir(run_id) / filename
        if not path.exists():
            raise FileNotFoundError("运行产物不存在")
        return path
