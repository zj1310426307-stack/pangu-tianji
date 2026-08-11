from __future__ import annotations

from typing import Any

import pandas as pd

from .ablation import analyze_ablation
from .contracts import FactorResearchConfig


class AblationEngine:
    """Compare the full score with leave-one-factor-out research portfolios."""

    engine_version = "factor-ablation-engine-v1.0.0"

    def run(self, panel: pd.DataFrame, config: FactorResearchConfig) -> dict[str, Any]:
        """Run the frozen-point ablation without changing production weights."""
        return analyze_ablation(
            panel,
            horizon=config.ablation_horizon,
            top_fraction=config.ablation_top_fraction,
            periods_per_year=config.annualization_periods,
        )
