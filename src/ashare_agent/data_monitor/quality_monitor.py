from __future__ import annotations

from typing import Any, Mapping

from .contracts import REQUIRED_ASSETS, component, issue


class DataQualityMonitor:
    """Check required assets, duplicate keys and impossible source values."""

    @staticmethod
    def _duplicates(rows: list[Mapping[str, Any]], keys: tuple[str, ...]) -> int:
        """Count duplicate composite keys while treating missing keys as invalid rows."""
        seen: set[tuple[str, ...]] = set()
        duplicates = 0
        for row in rows:
            key = tuple(str(row.get(name) or "") for name in keys)
            if key in seen:
                duplicates += 1
            seen.add(key)
        return duplicates

    def evaluate(
        self,
        manifest: Mapping[str, Any],
        assets: Mapping[str, Any],
        load_errors: Mapping[str, str],
    ) -> dict[str, Any]:
        """Return a completeness-oriented quality score from immutable run evidence."""
        problems: list[dict[str, Any]] = []
        manifest_assets = set((manifest.get("assets") or {}).keys())
        missing = sorted(REQUIRED_ASSETS - manifest_assets)
        if missing:
            problems.append(issue(
                "completeness", "REQUIRED_ASSET_MISSING", "BLOCKED",
                f"缺少{len(missing)}项关键Data Center资产", blocking=True,
                details={"assets": missing},
            ))
        if load_errors:
            problems.append(issue(
                "completeness", "ASSET_UNREADABLE", "BLOCKED",
                f"{len(load_errors)}项数据资产无法通过内容哈希读取", blocking=True,
                details={"assets": sorted(load_errors)},
            ))

        required_non_empty = (
            "raw_security_universe", "raw_market_snapshot", "raw_price_history",
            "security_master", "market_data_pit", "feature_store", "factor_store",
            "final_ranking",
        )
        empty = [name for name in required_non_empty if name in assets and not assets.get(name)]
        if empty:
            problems.append(issue(
                "completeness", "REQUIRED_ASSET_EMPTY", "BLOCKED",
                f"{len(empty)}项关键资产为空", blocking=True, details={"assets": empty},
            ))

        raw_universe = list(assets.get("raw_security_universe") or [])
        raw_snapshot = list(assets.get("raw_market_snapshot") or [])
        universe = list(assets.get("security_master") or [])
        snapshot = list(assets.get("market_data_pit") or [])
        history = list(assets.get("raw_price_history") or [])
        financials = list(assets.get("financial_point_in_time") or [])
        duplicates = {
            "raw_security_universe": self._symbol_duplicates(raw_universe),
            "raw_market_snapshot": self._symbol_duplicates(raw_snapshot),
            "security_master": self._duplicates(universe, ("symbol",)),
            "market_snapshot": self._duplicates(snapshot, ("symbol",)),
            "market_history": self._duplicates(history, ("date", "symbol")),
            "financial_point_in_time": self._duplicates(financials, ("available_at", "symbol")),
        }
        duplicate_total = sum(duplicates.values())
        if duplicate_total:
            problems.append(issue(
                "completeness", "DUPLICATE_PRIMARY_KEY", "ERROR",
                f"发现{duplicate_total}条重复主键", blocking=True, details=duplicates,
            ))

        invalid_price = sum(
            1 for row in snapshot
            if self._number(row.get("last_price")) is None or self._number(row.get("last_price")) <= 0
        )
        invalid_volume = sum(
            1 for row in snapshot
            if self._number(row.get("volume")) is None or self._number(row.get("volume")) < 0
        )
        if invalid_price:
            problems.append(issue(
                "market", "INVALID_PRICE", "BLOCKED",
                f"行情快照存在{invalid_price}条非正或无效价格", blocking=True,
                details={"count": invalid_price},
            ))
        if invalid_volume:
            problems.append(issue(
                "market", "INVALID_VOLUME", "BLOCKED",
                f"行情快照存在{invalid_volume}条负数或无效成交量", blocking=True,
                details={"count": invalid_volume},
            ))

        suspended_with_volume = sum(
            1 for row in raw_snapshot
            if self._explicitly_suspended(row)
            and (self._number(row.get("volume")) or 0) > 0
        )
        if suspended_with_volume:
            problems.append(issue(
                "market", "SUSPENSION_STATUS_CONFLICT", "BLOCKED",
                f"发现{suspended_with_volume}条停牌状态与成交量冲突记录",
                blocking=True, details={"count": suspended_with_volume},
            ))

        roe_missing = sum(1 for row in financials if self._roe(row) is None)
        roe_missing_ratio = roe_missing / len(financials) if financials else 1.0
        if financials and roe_missing:
            level = "BLOCKED" if roe_missing_ratio > 0.20 else "WARNING"
            problems.append(issue(
                "financial", "ROE_MISSING", level,
                f"财务数据ROE缺失率为{roe_missing_ratio:.1%}",
                blocking=level == "BLOCKED",
                details={"missing": roe_missing, "total": len(financials),
                         "ratio": round(roe_missing_ratio, 6)},
            ))
        extreme_profit = sum(
            1 for row in financials
            if any(
                self._number(row.get(name)) is not None
                and abs(self._number(row.get(name)) or 0) > 10_000
                for name in (
                    "net_profit_yoy_growth_ratio", "net_profit_growth",
                    "operating_income_yoy_growth_ratio", "revenue_growth",
                )
            )
        )
        if extreme_profit:
            problems.append(issue(
                "financial", "EXTREME_FINANCIAL_VALUE", "BLOCKED",
                f"发现{extreme_profit}条超过合理审查范围的财务增长值",
                blocking=True, details={"count": extreme_profit, "absolute_limit": 10_000},
            ))

        penalty = len(missing) * 20 + len(load_errors) * 25 + len(empty) * 20
        penalty += min(40, duplicate_total * 5) + min(60, invalid_price * 10 + invalid_volume * 10)
        penalty += min(30, suspended_with_volume * 10)
        penalty += min(30, roe_missing_ratio * 30 if financials else 0)
        penalty += min(40, extreme_profit * 20)
        checks = {
            "required_asset_count": len(REQUIRED_ASSETS),
            "present_asset_count": len(REQUIRED_ASSETS & manifest_assets),
            "missing_assets": missing,
            "unreadable_assets": sorted(load_errors),
            "empty_assets": empty,
            "duplicates": duplicates,
            "invalid_price_count": invalid_price,
            "invalid_volume_count": invalid_volume,
            "suspension_status_conflict_count": suspended_with_volume,
            "roe_missing_count": roe_missing,
            "roe_missing_ratio": round(roe_missing_ratio, 6),
            "extreme_financial_value_count": extreme_profit,
        }
        return component("completeness", 100 - penalty, checks, problems)

    @staticmethod
    def _number(value: Any) -> float | None:
        """Convert a source scalar to a finite float for deterministic validation."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None

    @staticmethod
    def _symbol_duplicates(rows: list[Mapping[str, Any]]) -> int:
        """Count provider symbol duplicates across raw and normalized field names."""
        seen: set[str] = set()
        duplicates = 0
        for row in rows:
            symbol = str(row.get("symbol") or row.get("thscode") or "")
            if not symbol:
                continue
            if symbol in seen:
                duplicates += 1
            seen.add(symbol)
        return duplicates

    @staticmethod
    def _explicitly_suspended(row: Mapping[str, Any]) -> bool:
        """Recognize only explicit provider suspension flags, never infer from no volume."""
        direct = row.get("suspended", row.get("is_suspended"))
        if isinstance(direct, bool):
            return direct
        if str(direct).strip().lower() in {"1", "true", "yes"}:
            return True
        return str(row.get("trade_status") or "").strip().lower() in {
            "suspended", "halted", "停牌",
        }

    def _roe(self, row: Mapping[str, Any]) -> float | None:
        """Read one of the supported provider/normalized ROE fields."""
        for name in ("roe", "index_weighted_avg_roe", "weighted_roe", "roe_ttm"):
            if name in row:
                return self._number(row.get(name))
        return None
