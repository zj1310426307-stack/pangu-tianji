from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import BinaryIO


class ProjectRunLock:
    """Own one operating-system file lock for all writers in a project."""

    _registry_guard = Lock()
    _held_paths: set[Path] = set()

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._handle: BinaryIO | None = None
        self.last_error: str | None = None

    def acquire(self) -> bool:
        """Acquire without waiting; return False when another writer is active."""
        with self._registry_guard:
            if self._handle is not None or self.path in self._held_paths:
                self.last_error = "本进程已有写入任务"
                return False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if "handle" in locals():
                    handle.close()
                self.last_error = f"{type(exc).__name__}: {exc}"
                return False
            self._handle = handle
            self._held_paths.add(self.path)
            self.last_error = None
            return True

    def release(self) -> None:
        """Release the OS lock; a crashed process is released by the OS itself."""
        with self._registry_guard:
            handle = self._handle
            if handle is None:
                return
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                self._handle = None
                self._held_paths.discard(self.path)

    @property
    def held(self) -> bool:
        """Return whether this instance currently owns the file lock."""
        return self._handle is not None
