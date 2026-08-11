from pathlib import Path

from ashare_agent.core.project_lock import ProjectRunLock


def test_project_lock_allows_only_one_writer(tmp_path: Path) -> None:
    path = tmp_path / "output" / ".paper_run.lock"
    first = ProjectRunLock(path)
    second = ProjectRunLock(path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()
