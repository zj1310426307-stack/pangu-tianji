from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import FactorResearchConfig
from .factor_correlation import analyze_factor_correlation


class CorrelationEngine:
    """Calculate cross-sectional correlation and descriptive redundancy evidence."""

    engine_version = "factor-correlation-engine-v1.0.0"

    def run(self, panel: pd.DataFrame, config: FactorResearchConfig) -> dict[str, Any]:
        """Return the averaged matrix and threshold-based review candidates."""
        return analyze_factor_correlation(
            panel,
            minimum_cross_section=config.minimum_cross_section,
            high_correlation_threshold=config.correlation_threshold,
        )
