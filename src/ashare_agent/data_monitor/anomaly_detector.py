from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from .contracts import component, issue


class DataAnomalyDetector:
    """Detect deterministic market, financial, universe and factor anomalies."""

    def evaluate(
        self,
        manifest: Mapping[str, Any],
        assets: Mapping[str, Any],
        baseline_assets: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare one run with its own history and an optional prior formal run."""
        problems: list[dict[str, Any]] = []
        notes: list[str] = []
        history = list(assets.get("raw_price_history") or [])
        financials = list(assets.get("financial_point_in_time") or [])
        factors = list(assets.get("factor_store") or [])
        universe = list(assets.get("security_master") or [])

        price_jumps = self._price_jumps(history)
        if price_jumps:
            problems.append(issue(
                "market", "EXTREME_PRICE_JUMP", "BLOCKED",
                f"发现{len(price_jumps)}个超过80%的相邻日价格跳变", blocking=True,
                details={"examples": price_jumps[:10]},
            ))
        future_financials = self._future_financials(
            financials, str(manifest.get("research_date") or "")
        )
        if future_financials:
            problems.append(issue(
                "financial", "FUTURE_FINANCIAL_DATA", "BLOCKED",
                f"发现{len(future_financials)}条研究日后才可见的财务数据", blocking=True,
                details={"examples": future_financials[:10]},
            ))
        invalid_scores = self._invalid_factor_scores(factors)
        if invalid_scores:
            problems.append(issue(
                "factor", "INVALID_FACTOR_SCORE", "BLOCKED",
                f"发现{len(invalid_scores)}条无效综合因子分", blocking=True,
                details={"examples": invalid_scores[:10]},
            ))

        drift: dict[str, Any] = {"available": False, "factor_median_shift": {}, "universe_change": None}
        if baseline_assets:
            drift = self._drift(assets, baseline_assets)
            blocking_factor = {
                name: value for name, value in drift["factor_median_shift"].items()
                if value >= 35
            }
            warning_factor = {
                name: value for name, value in drift["factor_median_shift"].items()
                if 20 <= value < 35
            }
            if blocking_factor:
                problems.append(issue(
                    "factor", "FACTOR_DISTRIBUTION_DRIFT", "BLOCKED",
                    "因子分布相对上一正式run发生严重漂移", blocking=True,
                    details={"median_shift": blocking_factor},
                ))
            elif warning_factor:
                problems.append(issue(
                    "factor", "FACTOR_DISTRIBUTION_WARNING", "WARNING",
                    "因子分布相对上一正式run发生明显漂移",
                    details={"median_shift": warning_factor},
                ))
            universe_change = drift.get("universe_change")
            if universe_change is not None and universe_change >= 0.50:
                problems.append(issue(
                    "universe", "UNIVERSE_SIZE_DRIFT", "BLOCKED",
                    f"股票池规模变化{universe_change:.1%}", blocking=True,
                    details={"change": universe_change},
                ))
            elif universe_change is not None and universe_change >= 0.30:
                problems.append(issue(
                    "universe", "UNIVERSE_SIZE_WARNING", "WARNING",
                    f"股票池规模变化{universe_change:.1%}",
                    details={"change": universe_change},
                ))
        else:
            notes.append("首个可比较run尚无历史漂移基线；不因此扣分或阻断")

        penalty = len(price_jumps) * 40 + len(future_financials) * 40 + len(invalid_scores) * 20
        if any(item["code"] == "FACTOR_DISTRIBUTION_DRIFT" for item in problems):
            penalty += 60
        elif any(item["code"] == "FACTOR_DISTRIBUTION_WARNING" for item in problems):
            penalty += 20
        if any(item["code"] == "UNIVERSE_SIZE_DRIFT" for item in problems):
            penalty += 50
        elif any(item["code"] == "UNIVERSE_SIZE_WARNING" for item in problems):
            penalty += 15
        checks = {
            "price_jump_count": len(price_jumps),
            "future_financial_count": len(future_financials),
            "invalid_factor_score_count": len(invalid_scores),
            "drift": drift,
            "universe_rows": len(universe),
        }
        return component("anomaly", 100 - penalty, checks, problems, notes=notes)

    @staticmethod
    def _number(value: Any) -> float | None:
        """Return a finite float or None for anomaly calculations."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None

    def _price_jumps(self, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Find implausible adjacent close-price ratios after sorting each symbol by date."""
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("symbol"):
                grouped[str(row["symbol"])].append(row)
        found: list[dict[str, Any]] = []
        for symbol, items in grouped.items():
            ordered = sorted(items, key=lambda item: str(item.get("date") or ""))
            previous: float | None = None
            for row in ordered:
                current = self._number(row.get("close"))
                if current is None or current <= 0:
                    continue
                if previous and abs(current / previous - 1) > 0.80:
                    found.append({
                        "symbol": symbol, "date": row.get("date"),
                        "previous_close": round(previous, 6), "close": round(current, 6),
                    })
                previous = current
        return found

    @staticmethod
    def _future_financials(rows: list[Mapping[str, Any]], research_date: str) -> list[dict[str, str]]:
        """Return financial records whose availability timestamp is in the future."""
        return [
            {"symbol": str(row.get("symbol") or ""), "available_at": str(row.get("available_at") or "")}
            for row in rows
            if str(row.get("available_at") or "")[:10] > research_date
        ]

    def _invalid_factor_scores(self, rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Validate the production aggregate factor score when it exists."""
        invalid: list[dict[str, Any]] = []
        for row in rows:
            if "score" not in row:
                continue
            score = self._number(row.get("score"))
            if score is None or score < 0 or score > 100:
                invalid.append({"symbol": row.get("symbol"), "score": row.get("score")})
        return invalid

    def _drift(
        self,
        current: Mapping[str, Any],
        baseline: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Compare stable score-column medians and universe size with the prior run."""
        current_factors = list(current.get("factor_store") or [])
        baseline_factors = list(baseline.get("factor_store") or [])
        common_columns = sorted(
            set().union(*(row.keys() for row in current_factors or [{}]))
            & set().union(*(row.keys() for row in baseline_factors or [{}]))
        )
        score_columns = [
            name for name in common_columns
            if name == "score" or str(name).endswith("_score")
        ]
        shifts: dict[str, float] = {}
        for name in score_columns:
            current_values = [self._number(row.get(name)) for row in current_factors]
            baseline_values = [self._number(row.get(name)) for row in baseline_factors]
            current_clean = [value for value in current_values if value is not None]
            baseline_clean = [value for value in baseline_values if value is not None]
            if current_clean and baseline_clean:
                shifts[name] = round(abs(median(current_clean) - median(baseline_clean)), 4)
        current_count = len(list(current.get("security_master") or []))
        baseline_count = len(list(baseline.get("security_master") or []))
        universe_change = (
            abs(current_count - baseline_count) / baseline_count if baseline_count else None
        )
        return {
            "available": True,
            "factor_median_shift": shifts,
            "universe_change": round(universe_change, 6) if universe_change is not None else None,
            "baseline_universe_count": baseline_count,
            "current_universe_count": current_count,
        }
