from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import FactorResearchConfig
from .ic_analysis import analyze_rank_ic


class RankICEngine:
    """Expose the versioned IC workflow while reusing the single tested implementation."""

    engine_version = "rank-ic-engine-v1.0.0"

    def run(self, panel: pd.DataFrame, config: FactorResearchConfig) -> dict[str, Any]:
        """Calculate all configured cross-sectional Rank IC horizons."""
        return analyze_rank_ic(
            panel,
            horizons=config.horizons,
            minimum_cross_section=config.minimum_cross_section,
        )
