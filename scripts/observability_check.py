"""Static observability safety, schema and frontend-boundary checks for CI."""

from __future__ import annotations

import ast
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pangu.observability.store import ObservabilityStore  # noqa: E402


def main() -> int:
    """Reject execution dependencies, unsafe schema flags and handwritten API calls."""
    errors: list[str] = []
    package = ROOT / "src" / "pangu" / "observability"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    forbidden = ("paper_portfolio", "place_order", "submit_order", "cancel_order", "broker.", "execution_rules")
    for token in forbidden:
        if token in text.lower():
            errors.append(f"forbidden observability dependency: {token}")
    for path in package.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    if "fetch(" in app or re.search(r"[\"']/api/", app):
        errors.append("frontend contains handwritten API request")
    with tempfile.TemporaryDirectory() as directory:
        store = ObservabilityStore(Path(directory) / "observability.db")
        with closing(sqlite3.connect(store.path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"metric_samples", "metric_rollups", "traces", "spans", "job_runs", "slo_definitions", "slo_evaluations", "alerts", "alert_events", "incidents", "incident_links", "observability_snapshots", "retention_runs"}
            if missing := required - tables:
                errors.append(f"missing observability tables: {sorted(missing)}")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                errors.append("unexpected observability migration version")
    print({"status": "failed" if errors else "passed", "errors": errors})
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
