from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from ..agent import TradingResearchAgent
from ..core.contracts import RuntimeState
from ..core.project_lock import ProjectRunLock
from ..repositories.run_repository import RunRepository


class RunConflictError(RuntimeError):
    """Signal that another research run already owns the single worker."""


class SafetyStopError(RuntimeError):
    """Signal that the in-memory safety stop blocks new research runs."""


class RunService:
    """Serialize paper backtests and expose runtime state without sharing SQLite."""

    def __init__(self, project_root: Path, repository: RunRepository | None = None) -> None:
        self.project_root = project_root.resolve()
        self.repository = repository or RunRepository(self.project_root)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-run")
        self._lock = Lock()
        self._state = RuntimeState.IDLE
        self._stage: str | None = None
        self._run_id: str | None = None
        self._started_at: str | None = None
        self._updated_at: str | None = None
        self._error_message: str | None = None
        self._kill_switch = False
        self._project_lock = ProjectRunLock(
            self.project_root / "output" / ".paper_run.lock"
        )
        # Only the process that proves exclusive ownership may recover stale runs.
        if self._project_lock.acquire():
            try:
                self.repository.recover_stale_runs()
            finally:
                self._project_lock.release()

    @staticmethod
    def _now() -> str:
        """Return a timezone-aware UTC timestamp for API state."""
        return datetime.now(timezone.utc).isoformat()

    def start_run(self) -> dict[str, Any]:
        """Reserve the only worker and queue one immutable paper-mode run."""
        with self._lock:
            if self._kill_switch:
                raise SafetyStopError("安全停止已触发，禁止启动新任务")
            if self._state == RuntimeState.RUNNING:
                raise RunConflictError("已有回测正在运行")
            if not self._project_lock.acquire():
                detail = self._project_lock.last_error or "锁已被占用"
                raise RunConflictError(
                    f"另一个本地进程正在写入本项目，请稍后再试（{detail}）"
                )
            run_id = str(uuid4())
            started_at = self._now()
            try:
                self.repository.create_run(run_id, started_at)
                self._state = RuntimeState.RUNNING
                self._stage = "queued"
                self._run_id = run_id
                self._started_at = started_at
                self._updated_at = started_at
                self._error_message = None
                self._executor.submit(self._execute, run_id)
                result = self._status_unlocked()
            except Exception:
                self._project_lock.release()
                raise
        return result

    def _set_stage(self, run_id: str, stage: str) -> None:
        """Synchronize observable in-memory and durable stage state."""
        self.repository.update_stage(run_id, stage)
        with self._lock:
            if self._run_id == run_id:
                self._stage = stage
                self._updated_at = self._now()

    def _execute(self, run_id: str) -> None:
        """Run the existing CLI pipeline in the single background worker."""
        try:
            self._set_stage(run_id, "preparing_data")
            agent = TradingResearchAgent(
                self.repository.settings_snapshot_path(run_id),
                project_root=self.project_root,
            )
            self._set_stage(run_id, "running_backtest")
            metrics = agent.run()
            self._set_stage(run_id, "saving_snapshot")
            record = self.repository.complete_run(run_id, metrics)
            with self._lock:
                self._state = RuntimeState.SUCCEEDED
                self._stage = "completed"
                self._updated_at = record["completed_at"]
        except Exception as exc:  # Background failures must become durable state.
            try:
                record = self.repository.fail_run(run_id, str(exc))
                completed_at = record["completed_at"]
            except Exception:
                completed_at = self._now()
            with self._lock:
                self._state = RuntimeState.FAILED
                self._stage = "failed"
                self._updated_at = completed_at
                self._error_message = str(exc)[:1000]
        finally:
            self._project_lock.release()

    def status(self) -> dict[str, Any]:
        """Return a thread-safe runtime snapshot independent of safety/model state."""
        with self._lock:
            return self._status_unlocked()

    def _status_unlocked(self) -> dict[str, Any]:
        """Build a runtime snapshot while the caller already owns the lock."""
        return {
            "state": self._state.value,
            "stage": self._stage,
            "run_id": self._run_id,
            "started_at": self._started_at,
            "updated_at": self._updated_at,
            "error_message": self._error_message,
        }

    @property
    def kill_switch(self) -> bool:
        """Return whether new runs are blocked by the local safety stop."""
        with self._lock:
            return self._kill_switch

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        """Block or allow future runs without modifying live-trading settings."""
        with self._lock:
            self._kill_switch = bool(enabled)
            current = self._kill_switch
        return {"enabled": current, "live_trading_enabled": False}

    def shutdown(self) -> None:
        """Release the background executor during application teardown."""
        self._executor.shutdown(wait=False, cancel_futures=False)
