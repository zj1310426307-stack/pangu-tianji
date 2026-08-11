"""Run PANGU V3's idempotent daily strategy-health observation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

# Preserve the supported ASCII junction and make the source package importable
# without requiring an editable installation in the local virtual environment.
PROJECT_ROOT = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_agent.strategy_evolution import StrategyEvolutionService  # noqa: E402


def main() -> int:
    """Observe active registered strategies without changing any strategy or order."""
    service = StrategyEvolutionService(PROJECT_ROOT)
    result = service.observe_registered_strategies()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Missing sealed evidence is an honest data gap, not permission to fabricate it.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
