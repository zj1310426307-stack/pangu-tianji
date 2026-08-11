from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import FactorResearchConfig
from .factor_return import analyze_factor_returns
from .quantile_analysis import analyze_quantiles


class QuantileEngine:
    """Own factor-return and five/ten-group orchestration without duplicating formulas."""

    engine_version = "factor-quantile-engine-v1.0.0"

    def run(self, panel: pd.DataFrame, config: FactorResearchConfig) -> dict[str, Any]:
        """Return factor-mimicking and grouped forward-return research together."""
        return {
            "factor_returns": analyze_factor_returns(
                panel,
                horizons=config.horizons,
                periods_per_year=config.annualization_periods,
            ),
            "quantiles": analyze_quantiles(
                panel,
                horizons=config.horizons,
                quantile_counts=config.quantile_counts,
                minimum_cross_section=config.minimum_cross_section,
                periods_per_year=config.annualization_periods,
            ),
        }
