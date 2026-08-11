from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping


class DataLineageService:
    """Build a read-only lineage graph from source data to experiments and reports."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)

    def build(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Create nodes and edges without copying or mutating any upstream artifact."""
        run_id = str(manifest["run_id"])
        source = str(manifest.get("data_source") or "unknown")
        data_version = str(manifest.get("data_version") or "unknown")
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []

        def node(node_id: str, node_type: str, label: str, **metadata: Any) -> None:
            nodes[node_id] = {
                "node_id": node_id, "node_type": node_type, "label": label,
                "metadata": metadata,
            }

        def edge(from_id: str, to_id: str, relation: str) -> None:
            edges.append({"from_id": from_id, "to_id": to_id, "relation": relation})

        source_id = f"source:{source}"
        version_id = f"data-version:{data_version}"
        run_node = f"data-run:{run_id}"
        node(source_id, "data_source", source)
        node(version_id, "data_version", data_version)
        node(run_node, "research_run", run_id, research_date=manifest.get("research_date"))
        edge(source_id, version_id, "produces")
        edge(version_id, run_node, "materializes")

        layer_heads: dict[str, list[str]] = {}
        for asset_name, asset in sorted((manifest.get("assets") or {}).items()):
            asset_id = f"asset:{run_id}:{asset_name}"
            layer = str(asset.get("layer") or "unknown")
            node(
                asset_id, "data_asset", asset_name, layer=layer, model=asset.get("model"),
                sha256=asset.get("sha256"), rows=asset.get("rows"),
            )
            edge(run_node, asset_id, "contains")
            layer_heads.setdefault(layer, []).append(asset_id)

        for raw in layer_heads.get("raw", []):
            for clean in layer_heads.get("clean", []):
                edge(raw, clean, "normalizes_to")
        for clean in layer_heads.get("clean", []):
            for pit in layer_heads.get("point_in_time", []):
                edge(clean, pit, "point_in_time_to")
        for pit in layer_heads.get("point_in_time", []):
            for feature in layer_heads.get("features", []):
                edge(pit, feature, "feeds")
        factor_id = f"asset:{run_id}:factor_store"
        ranking_id = f"asset:{run_id}:final_ranking"
        portfolio_id = f"asset:{run_id}:portfolio_weights"
        if factor_id in nodes and ranking_id in nodes:
            edge(factor_id, ranking_id, "ranks")
        if ranking_id in nodes and portfolio_id in nodes:
            edge(ranking_id, portfolio_id, "constructs")

        experiment_ids = self._linked_experiments(run_id)
        for experiment_id in experiment_ids:
            experiment_node = f"experiment:{experiment_id}"
            node(experiment_node, "experiment", experiment_id)
            edge(run_node, experiment_node, "used_by")
            for report in self._experiment_reports(experiment_id):
                report_node = f"report:{report['report_id']}"
                node(report_node, report["node_type"], report["report_id"])
                edge(experiment_node, report_node, "explained_by")

        return {
            "run_id": run_id,
            "nodes": list(nodes.values()),
            "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges)},
        }

    def _linked_experiments(self, data_center_run_id: str) -> list[str]:
        """Find Quant Lab experiment specs that explicitly cite a Data Center run."""
        path = self.root / "output" / "quant_lab" / "experiments.sqlite3"
        if not path.exists():
            return []
        try:
            with sqlite3.connect(path) as connection:
                rows = connection.execute("SELECT experiment_id,spec_json FROM experiments").fetchall()
        except sqlite3.Error:
            return []
        found: list[str] = []
        for experiment_id, raw_spec in rows:
            try:
                run_ids = json.loads(raw_spec).get("data_center_run_ids") or []
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if data_center_run_id in {str(item) for item in run_ids}:
                found.append(str(experiment_id))
        return sorted(set(found))

    def _experiment_reports(self, experiment_id: str) -> list[dict[str, str]]:
        """Find sealed factor, robustness and AI reports associated with one experiment."""
        reports: list[dict[str, str]] = []
        registry = self.root / "output" / "quant_lab" / "experiments.sqlite3"
        if registry.exists():
            try:
                with sqlite3.connect(registry) as connection:
                    for table, node_type in (
                        ("factor_reports", "factor_report"),
                        ("robustness_reports", "robustness_report"),
                    ):
                        rows = connection.execute(
                            f"SELECT report_id FROM {table} WHERE experiment_id=?", (experiment_id,),
                        ).fetchall()
                        reports.extend(
                            {"report_id": str(row[0]), "node_type": node_type} for row in rows
                        )
            except sqlite3.Error:
                pass
        agent_db = self.root / "output" / "agent.db"
        if agent_db.exists():
            try:
                with sqlite3.connect(agent_db) as connection:
                    rows = connection.execute(
                        "SELECT report_id FROM ai_research_reports WHERE experiment_id=?",
                        (experiment_id,),
                    ).fetchall()
                reports.extend(
                    {"report_id": str(row[0]), "node_type": "ai_research_report"}
                    for row in rows
                )
            except sqlite3.Error:
                pass
        return sorted(reports, key=lambda item: (item["node_type"], item["report_id"]))
