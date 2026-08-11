"""Export FastAPI's OpenAPI contract and generate the tiny browser client.

The browser never hand-writes endpoint paths.  This script treats the API
contract as the source of truth and creates a dependency-free ES module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


# Preserve an ASCII junction when Windows' legacy-codepage Python cannot round-trip
# the real project path. This remains an absolute path but deliberately avoids
# resolving filesystem links.
ROOT = Path(os.path.abspath(__file__)).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ashare_agent.api.app import create_app  # noqa: E402


class _OpenApiDailyService:
    """Avoid creating mutable paper-account state while exporting the contract."""

    def close(self) -> None:
        """Match the application lifespan contract without owning resources."""


class _OpenApiWorkbenchService:
    """Supply schema-time dependency injection without opening a paper database."""

    def get(self) -> dict[str, Any]:
        """Reject runtime use because this stub exists only for OpenAPI export."""
        raise RuntimeError("OPENAPI_EXPORT_ONLY")


class _OpenApiCopilotService:
    """Avoid opening AI audit databases while exporting static API schemas."""


class _OpenApiInvestmentOSService:
    """Expose constructor-only dependencies without creating operating assets."""

    center = object()

    def close(self) -> None:
        """Match the application lifespan contract without owning resources."""


class _OpenApiStrategyValidationService:
    """Avoid opening strategy-governance databases during schema export."""

    registry = object()
    approvals = object()

    def dashboard(self) -> dict[str, Any]:
        return {
            "service_version": "strategy-validation-service-v1.0.0",
            "gate_contract": {},
            "strategies": [],
            "reviews": [],
            "promotion_history": [],
            "counts": {},
            "safety": {},
            "can_trade": False,
            "can_create_orders": False,
            "can_auto_promote": False,
        }


class _OpenApiQuantAIService:
    """Avoid opening AI research and experiment stores during schema export."""

    store = object()

    def dashboard(self) -> dict[str, Any]:
        return {
            "service_version": "ai-quant-research-analyst-v1.0.0",
            "generated_at": "1970-01-01T00:00:00+00:00",
            "model": {},
            "evidence": {},
            "latest_report": None,
            "reports": [],
            "memory": [],
            "questions": [],
            "tasks": [],
            "counts": {},
            "schedule": {},
            "agents": [],
            "safety": {},
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_experiment": False,
            "can_modify_parameters": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_approve_strategy": False,
        }


class _OpenApiPersonalOSService:
    """Prevent schema export from opening the Personal OS SQLite store."""


class _OpenApiDataIntelligenceService:
    """Prevent schema export from opening Data Center or monitoring databases."""


class _OpenApiStrategyEvolutionService:
    """Prevent schema export from opening strategy-evolution evidence stores."""

    def dashboard(self) -> dict[str, Any]:
        """Return the exact static response contract used by OpenAPI export."""
        return {
            "service_version": "strategy-evolution-engine-v1.0.0",
            "generated_at": "1970-01-01T00:00:00+00:00",
            "branches": [],
            "health_history": [],
            "comparisons": [],
            "lifecycle_history": [],
            "reports": [],
            "available_validation_strategies": [],
            "counts": {},
            "ai_strategy_observer": {},
            "safety": {},
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_parameters": False,
            "can_modify_factor_weights": False,
            "can_replace_production_strategy": False,
            "can_auto_transition": False,
            "can_launch_experiment": False,
        }



def collect_operations(schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return operation-id metadata in deterministic order."""
    operations: dict[str, dict[str, str]] = {}
    for path, path_item in sorted(schema.get("paths", {}).items()):
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not operation:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                raise RuntimeError(f"Missing operationId: {method.upper()} {path}")
            if operation_id in operations:
                raise RuntimeError(f"Duplicate operationId: {operation_id}")
            operations[operation_id] = {"method": method.upper(), "path": path}
    return dict(sorted(operations.items()))


def render_client(operations: dict[str, dict[str, str]]) -> str:
    """Render a small, safe client consumed by the static dashboard."""
    encoded = json.dumps(operations, ensure_ascii=False, indent=2)
    return f'''// Generated by scripts/sync_openapi.py. Do not edit by hand.
export const OPERATIONS = Object.freeze({encoded});

export function operationUrl(operationId, pathParams = {{}}, queryParams = {{}}) {{
  const operation = OPERATIONS[operationId];
  if (!operation) throw new Error(`未知接口：${{operationId}}`);
  let path = operation.path.replace(/[{{]([^}}]+)[}}]/g, (_match, name) => {{
    if (pathParams[name] === undefined || pathParams[name] === null) {{
      throw new Error(`缺少路径参数：${{name}}`);
    }}
    return encodeURIComponent(String(pathParams[name]));
  }});
  const query = new URLSearchParams();
  Object.entries(queryParams).forEach(([key, value]) => {{
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }});
  if (query.size) path += `?${{query.toString()}}`;
  return path;
}}

function errorMessage(payload, status) {{
  const detail = payload && payload.detail;
  if (detail && typeof detail === "object" && detail.message) return detail.message;
  if (typeof detail === "string") return detail;
  return `本地服务请求失败（HTTP ${{status}}）`;
}}

export async function apiRequest(operationId, options = {{}}) {{
  const operation = OPERATIONS[operationId];
  if (!operation) throw new Error(`未知接口：${{operationId}}`);

  const path = operationUrl(operationId, options.pathParams || {{}}, options.query || {{}});

  const init = {{ method: operation.method, headers: {{ Accept: "application/json" }} }};
  const mobileApi = operation.path.startsWith("/api/mobile/v1/");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(operation.method) && !mobileApi) {{
    init.headers["X-Ashare-Client"] = "local-dashboard";
  }}
  const accessToken = options.accessToken || options.token;
  if (accessToken) init.headers.Authorization = `Bearer ${{String(accessToken)}}`;
  if (options.body !== undefined) {{
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }}

  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    Number(options.timeoutMs || 15000),
  );
  init.signal = controller.signal;
  try {{
    const response = await fetch(path, init);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) throw new Error(errorMessage(payload, response.status));
    return payload;
  }} catch (error) {{
    if (error && error.name === "AbortError") throw new Error("本地服务请求超时");
    throw error;
  }} finally {{
    window.clearTimeout(timeout);
  }}
}}
'''


def main() -> None:
    """Synchronize both human/tool and browser representations of the API."""
    app = create_app(
        ROOT,
        daily_research_service=_OpenApiDailyService(),
        workbench_service=_OpenApiWorkbenchService(),
        copilot_service=_OpenApiCopilotService(),
        investment_os_service=_OpenApiInvestmentOSService(),
        strategy_validation_service=_OpenApiStrategyValidationService(),
        quant_ai_service=_OpenApiQuantAIService(),
        personal_os_service=_OpenApiPersonalOSService(),
        data_intelligence_service=_OpenApiDataIntelligenceService(),
        strategy_evolution_service=_OpenApiStrategyEvolutionService(),
    )
    schema = app.openapi()
    operations = collect_operations(schema)

    docs_dir = ROOT / "docs"
    generated_dir = ROOT / "web" / "generated"
    docs_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "openapi.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (generated_dir / "client.js").write_text(
        render_client(operations), encoding="utf-8"
    )
    print(f"Synced {len(operations)} API operations")


if __name__ == "__main__":
    main()
