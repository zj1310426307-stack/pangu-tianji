"""Pangu V2 descriptive factor research with immutable Quant Lab evidence."""

from .contracts import (
    FACTOR_COLUMNS,
    FACTOR_NAMES,
    FactorResearchConfig,
    FactorResearchResult,
)
from .service import FactorResearchService
from .ablation_engine import AblationEngine
from .correlation_engine import CorrelationEngine
from .decay_engine import DecayEngine
from .ic_engine import RankICEngine
from .quantile_engine import QuantileEngine
from .regime_engine import RegimeEngine
from .report_generator import FactorReportGenerator

__all__ = [
    "FACTOR_COLUMNS",
    "FACTOR_NAMES",
    "FactorResearchConfig",
    "FactorResearchResult",
    "FactorResearchService",
    "AblationEngine",
    "CorrelationEngine",
    "DecayEngine",
    "FactorReportGenerator",
    "QuantileEngine",
    "RankICEngine",
    "RegimeEngine",
]
