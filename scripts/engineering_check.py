"""Dependency-free engineering checks used locally and in GitHub Actions."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def check_version_contracts(errors: list[str]) -> None:
    """Verify protected runtime constants match the canonical registry."""
    from ashare_agent.factor_model import FACTOR_MODEL_VERSION
    from ashare_agent.research_pipeline import STRATEGY_VERSION
    from pangu.version.factor_versions import PRODUCTION_FACTOR_VERSION
    from pangu.version.strategy_versions import PRODUCTION_STRATEGY_VERSION

    if STRATEGY_VERSION != PRODUCTION_STRATEGY_VERSION:
        errors.append("production strategy version drift")
    if FACTOR_MODEL_VERSION != PRODUCTION_FACTOR_VERSION:
        errors.append("production factor version drift")


def check_exception_swallowing(errors: list[str]) -> None:
    """Reject newly introduced broad exception handlers that only pass."""
    for path in (ROOT / "src" / "pangu").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                continue
            type_name = getattr(node.type, "id", None)
            if node.type is None or type_name in {"Exception", "BaseException"}:
                errors.append(f"broad exception swallowed: {path.relative_to(ROOT)}:{node.lineno}")


def check_frontend_contract(errors: list[str]) -> None:
    """Keep handwritten frontend code behind the generated API client."""
    for path in (ROOT / "web").rglob("*.js"):
        if path.name == "client.js" and path.parent.name == "generated":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bfetch\s*\(", text):
            errors.append(f"handwritten fetch found: {path.relative_to(ROOT)}")
        if re.search(r"[\"']\/api\/(?:v1|mobile)", text):
            errors.append(f"handwritten API path found: {path.relative_to(ROOT)}")


def check_secret_files(errors: list[str]) -> None:
    """Reject known credential files and literal API keys in tracked sources."""
    forbidden_names = {".env", ".env.local", ".env.production", "credentials.json"}
    scan_roots = [ROOT / "config", ROOT / "src", ROOT / "scripts", ROOT / ".github"]
    literal_key = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
    for base in scan_roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.name.lower() in forbidden_names:
                errors.append(f"credential file found: {path.relative_to(ROOT)}")
                continue
            if path.suffix.lower() not in {".py", ".yaml", ".yml", ".json", ".md"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if literal_key.search(text):
                errors.append(f"literal API key found: {path.relative_to(ROOT)}")


def check_openapi_client(errors: list[str]) -> None:
    """Ensure each OpenAPI operation is present in the generated client."""
    schema = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    client = (ROOT / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    operation_ids = {
        operation["operationId"]
        for path_item in schema.get("paths", {}).values()
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    missing = sorted(item for item in operation_ids if f'"{item}"' not in client)
    if missing:
        errors.append(f"generated client missing operations: {missing}")


def main() -> int:
    """Run all deterministic checks and return a CI-compatible exit code."""
    errors: list[str] = []
    check_version_contracts(errors)
    check_exception_swallowing(errors)
    check_frontend_contract(errors)
    check_secret_files(errors)
    check_openapi_client(errors)
    print(json.dumps({"status": "passed" if not errors else "failed", "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
