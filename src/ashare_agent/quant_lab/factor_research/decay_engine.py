from __future__ import annotations

from typing import Any

from .decay_analysis import analyze_decay


class DecayEngine:
    """Build the descriptive horizon curve from already calculated IC and quantiles."""

    engine_version = "factor-decay-engine-v1.0.0"

    def run(
        self, ic_result: dict[str, Any], quantile_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Combine IC and quintile spread evidence without selecting a rebalance horizon."""
        return analyze_decay(ic_result, quantile_result)
