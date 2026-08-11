from __future__ import annotations

from typing import Any, Mapping

from .contracts import component, issue


class DataCoverageMonitor:
    """Measure market, history, financial and factor coverage with fixed thresholds."""

    def evaluate(self, manifest: Mapping[str, Any], assets: Mapping[str, Any]) -> dict[str, Any]:
        """Return coverage ratios and fail closed on material research gaps."""
        # Market coverage must use raw provider assets. Clean/PIT frames have
        # already passed strategy filters, so comparing them would confuse a
        # deliberate universe reduction with a provider data gap.
        universe = list(assets.get("raw_security_universe") or [])
        snapshot = list(assets.get("raw_market_snapshot") or [])
        history = list(assets.get("raw_price_history") or [])
        features = list(assets.get("feature_store") or [])
        factors = list(assets.get("factor_store") or [])
        financials = list(assets.get("financial_point_in_time") or [])

        universe_symbols = self._symbols(universe)
        snapshot_symbols = self._symbols(snapshot)
        history_symbols = self._symbols(history)
        feature_symbols = self._symbols(features)
        factor_symbols = self._symbols(factors)
        financial_symbols = self._symbols(financials)
        market_ratio = self._ratio(len(snapshot_symbols & universe_symbols), len(universe_symbols))
        history_ratio = self._ratio(len(history_symbols & feature_symbols), len(feature_symbols))
        valid_factor_symbols = {
            str(row.get("symbol")) for row in factors
            if row.get("symbol") and self._finite_number(row.get("score")) is not None
        }
        # Factor Score intentionally runs on the financially enriched subset.
        # Its coverage therefore means valid factor rows within factor_store,
        # not factor rows divided by the earlier technical prefilter.
        factor_ratio = self._ratio(len(valid_factor_symbols), len(factor_symbols))
        quality = dict(manifest.get("data_quality") or {})
        declared_financial = self._finite_ratio(quality.get("financial_coverage"))
        measured_financial = self._ratio(len(financial_symbols & feature_symbols), len(feature_symbols))
        financial_ratio = declared_financial if declared_financial is not None else measured_financial

        ratios = {
            "market": market_ratio,
            "history": history_ratio,
            "financial": financial_ratio,
            "factor": factor_ratio,
        }
        problems: list[dict[str, Any]] = []
        thresholds = {"market": 0.90, "history": 0.95, "financial": 0.80, "factor": 0.95}
        labels = {"market": "行情", "history": "历史日线", "financial": "财务", "factor": "因子"}
        for name, minimum in thresholds.items():
            ratio = ratios[name]
            if ratio < minimum:
                problems.append(issue(
                    "coverage", f"{name.upper()}_COVERAGE_LOW", "BLOCKED",
                    f"{labels[name]}覆盖率{ratio:.1%}低于门禁{minimum:.0%}", blocking=True,
                    details={"coverage": ratio, "minimum": minimum},
                ))
            elif ratio < 0.98:
                problems.append(issue(
                    "coverage", f"{name.upper()}_COVERAGE_WARNING", "WARNING",
                    f"{labels[name]}覆盖率为{ratio:.1%}", blocking=False,
                    details={"coverage": ratio, "target": 0.98},
                ))
        score = sum(ratios.values()) / len(ratios) * 100 if ratios else 0
        checks = {
            "ratios": {key: round(value, 6) for key, value in ratios.items()},
            "counts": {
                "universe": len(universe_symbols), "snapshot": len(snapshot_symbols),
                "history": len(history_symbols), "features": len(feature_symbols),
                "factors": len(factor_symbols), "financials": len(financial_symbols),
            },
            "thresholds": thresholds,
        }
        return component("coverage", score, checks, problems)

    @staticmethod
    def _symbols(rows: list[Mapping[str, Any]]) -> set[str]:
        """Return non-empty normalized symbols from one asset."""
        return {
            str(row.get("symbol") or row.get("thscode"))
            for row in rows
            if row.get("symbol") or row.get("thscode")
        }

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        """Return a bounded ratio and treat an empty denominator as missing coverage."""
        if denominator <= 0:
            return 0.0
        return max(0.0, min(1.0, numerator / denominator))

    @staticmethod
    def _finite_ratio(value: Any) -> float | None:
        """Normalize a provider-declared coverage ratio when present."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or abs(number) == float("inf"):
            return None
        return max(0.0, min(1.0, number))

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        """Return one finite numeric factor value without changing source data."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None
