from __future__ import annotations

from typing import Any


def analyze_decay(ic_result: dict[str, Any], quantile_result: dict[str, Any]) -> dict[str, Any]:
    """Combine IC and long-short horizon statistics into a descriptive decay curve."""
    factors: dict[str, Any] = {}
    for factor_name, horizons in ic_result["factors"].items():
        curve: list[dict[str, Any]] = []
        for horizon_text, ic_metrics in sorted(horizons.items(), key=lambda item: int(item[0])):
            spread = quantile_result["factors"][factor_name]["5"][horizon_text]["long_short"]
            curve.append({
                "horizon": int(horizon_text),
                "ic_mean": ic_metrics["ic_mean"],
                "absolute_ic_mean": abs(ic_metrics["ic_mean"]) if ic_metrics["ic_mean"] is not None else None,
                "icir": ic_metrics["icir"],
                "long_short_mean_forward_return": spread["mean_forward_return"],
                "observation_count": ic_metrics["observation_count"],
            })
        available = [row for row in curve if row["absolute_ic_mean"] is not None]
        peak = max(available, key=lambda row: row["absolute_ic_mean"]) if available else None
        peak_value = peak["absolute_ic_mean"] if peak else None
        for row in curve:
            row["relative_to_peak_absolute_ic"] = (
                row["absolute_ic_mean"] / peak_value
                if peak_value not in {None, 0.0} and row["absolute_ic_mean"] is not None else None
            )
        factors[factor_name] = {
            "availability": "available" if available else "unavailable",
            "peak_horizon": peak["horizon"] if peak else None,
            "curve": curve,
        }
    return {
        "method": "absolute_rank_ic_and_quintile_long_short_by_forward_horizon",
        "interpretation": "descriptive_only_no_rebalance_recommendation",
        "factors": factors,
    }
