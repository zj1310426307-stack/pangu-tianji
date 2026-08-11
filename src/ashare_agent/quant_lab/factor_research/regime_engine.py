from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import FactorResearchConfig
from .stability import analyze_stability


class RegimeEngine:
    """Compare factor statistics across explicit point-in-time market regimes."""

    engine_version = "factor-regime-engine-v1.0.0"

    def run(self, panel: pd.DataFrame, config: FactorResearchConfig) -> dict[str, Any]:
        """Analyze bull, bear and sideways labels without inferring them from future returns."""
        return analyze_stability(
            panel,
            horizons=config.horizons,
            minimum_cross_section=config.minimum_cross_section,
            periods_per_year=config.annualization_periods,
        )
