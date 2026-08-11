from __future__ import annotations

from itertools import combinations
import math
from statistics import NormalDist
from typing import Mapping

import numpy as np

from .contracts import RobustnessConfig, StrategyPath
from .performance import net_returns_after_cost


def _sharpe(values: np.ndarray, periods: int) -> float:
    """Calculate annualized zero-rate Sharpe for one trial slice."""
    standard_deviation = float(values.std(ddof=0))
    return float(values.mean() / standard_deviation * math.sqrt(periods)) if standard_deviation > 0 else 0.0


def _net(path: StrategyPath, config: RobustnessConfig) -> np.ndarray:
    """Apply the common baseline cost contract before all overfitting statistics."""
    return net_returns_after_cost(
        path.gross_returns, path.turnovers,
        commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )


def analyze_overfitting(
    paths: Mapping[str, StrategyPath], config: RobustnessConfig
) -> dict:
    """Estimate DSR and CSCV-PBO while refusing underpowered samples."""
    trial_ids = sorted(name for name in paths if name == "baseline" or name.startswith("parameter."))
    trial_count = len(trial_ids)
    observation_count = len(paths["baseline"].dates)
    result = {
        "trial_count": trial_count,
        "parameter_trial_count_recorded": True,
        "selected_for_production": False,
        "deflated_sharpe_ratio": {"availability": "UNAVAILABLE"},
        "pbo": {"availability": "UNAVAILABLE"},
    }
    if trial_count < 2 or observation_count < config.minimum_overfit_observations:
        reason = "insufficient_trials_or_observations"
        result["deflated_sharpe_ratio"]["reason"] = reason
        result["pbo"]["reason"] = reason
        result["availability"] = "UNAVAILABLE"
        return result
    matrix = np.column_stack([_net(paths[name], config) for name in trial_ids])
    if any(len(paths[name].dates) != observation_count for name in trial_ids):
        result["deflated_sharpe_ratio"]["reason"] = "misaligned_trial_paths"
        result["pbo"]["reason"] = "misaligned_trial_paths"
        result["availability"] = "UNAVAILABLE"
        return result
    trial_sharpes = np.asarray([
        _sharpe(matrix[:, index], 1)
        for index in range(trial_count)
    ])
    baseline = matrix[:, trial_ids.index("baseline")]
    baseline_sharpe = _sharpe(baseline, 1)
    sharpe_std = float(trial_sharpes.std(ddof=0))
    if sharpe_std > 0:
        gamma = 0.5772156649015329
        normal = NormalDist()
        expected_max = sharpe_std * (
            (1 - gamma) * normal.inv_cdf(1 - 1 / trial_count)
            + gamma * normal.inv_cdf(1 - 1 / (trial_count * math.e))
        )
        centered = baseline - baseline.mean()
        std = float(baseline.std(ddof=0))
        skew = float(np.mean((centered / std) ** 3)) if std > 0 else 0.0
        kurtosis = float(np.mean((centered / std) ** 4)) if std > 0 else 3.0
        denominator = math.sqrt(max(
            1e-12,
            1 - skew * baseline_sharpe + ((kurtosis - 1) / 4) * baseline_sharpe ** 2,
        ))
        probability = normal.cdf(
            (baseline_sharpe - expected_max) * math.sqrt(observation_count - 1) / denominator
        )
        result["deflated_sharpe_ratio"] = {
            "availability": "AVAILABLE",
            "baseline_periodic_sharpe": baseline_sharpe,
            "baseline_annualized_sharpe": baseline_sharpe * math.sqrt(config.annualization_periods),
            "expected_max_periodic_sharpe_under_multiple_trials": float(expected_max),
            "expected_max_annualized_sharpe_under_multiple_trials": float(
                expected_max * math.sqrt(config.annualization_periods)
            ),
            "probability": float(probability),
            "trial_count": trial_count,
            "observation_count": observation_count,
            "method": "probabilistic_sharpe_deflated_for_multiple_trials",
        }
    else:
        result["deflated_sharpe_ratio"]["reason"] = "zero_cross_trial_sharpe_dispersion"
    slices = config.cscv_slices
    if observation_count < slices * 2:
        result["pbo"]["reason"] = "insufficient_observations_for_cscv"
    else:
        partitions = [item for item in np.array_split(np.arange(observation_count), slices) if len(item)]
        logits = []
        for in_slice_ids in combinations(range(slices), slices // 2):
            in_set = set(in_slice_ids)
            in_indices = np.concatenate([partitions[index] for index in sorted(in_set)])
            out_indices = np.concatenate([
                partitions[index] for index in range(slices) if index not in in_set
            ])
            in_sharpes = np.asarray([
                _sharpe(matrix[in_indices, index], 1)
                for index in range(trial_count)
            ])
            chosen = int(np.argmax(in_sharpes))
            out_sharpes = np.asarray([
                _sharpe(matrix[out_indices, index], 1)
                for index in range(trial_count)
            ])
            relative_rank = (float(np.sum(out_sharpes <= out_sharpes[chosen])) - 0.5) / trial_count
            relative_rank = min(max(relative_rank, 1e-9), 1 - 1e-9)
            logits.append(math.log(relative_rank / (1 - relative_rank)))
        pbo = float(np.mean(np.asarray(logits) <= 0))
        result["pbo"] = {
            "availability": "AVAILABLE",
            "probability": pbo,
            "cscv_slices": slices,
            "combination_count": len(logits),
            "method": "combinatorially_symmetric_cross_validation",
        }
    dsr_ok = result["deflated_sharpe_ratio"]["availability"] == "AVAILABLE"
    pbo_ok = result["pbo"]["availability"] == "AVAILABLE"
    result["availability"] = "AVAILABLE" if dsr_ok and pbo_ok else "PARTIAL"
    return result
