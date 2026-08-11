from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from .contracts import (
    DatasetValidation,
    ExperimentSpec,
    ValidationEvent,
    ValidationStatus,
    aggregate_dataset_version,
    utc_now,
)


_SECRET = re.compile(r"(?:api[_-]?key|authorization|bearer|password|secret|token|credential)", re.I)


@dataclass(frozen=True)
class DatasetValidationPolicy:
    """Define fail-closed evidence and coverage gates without changing strategy math."""

    minimum_financial_coverage: float = 0.50
    require_listing_history: bool = True
    require_delisting_history: bool = True
    require_trading_status: bool = True
    require_limit_prices: bool = True
    require_corporate_actions: bool = False


class DatasetValidator:
    """Validate Data Center manifests, assets and point-in-time relationships."""

    def __init__(self, data_center_root: Path, policy: DatasetValidationPolicy | None = None) -> None:
        self.root = data_center_root
        self.catalog = self.root / "catalog.sqlite3"
        self.policy = policy or DatasetValidationPolicy()

    def validate(self, spec: ExperimentSpec) -> DatasetValidation:
        """Return a deterministic evidence decision for all requested Data Center runs."""
        events: list[ValidationEvent] = []
        if not self.catalog.exists():
            events.append(self._event("CATALOG_MISSING", ValidationStatus.BLOCKED, "Data Center catalog不存在"))
            return self._result(spec, events)
        with sqlite3.connect(self.catalog) as connection:
            rows = connection.execute(
                f"SELECT run_id,data_version FROM research_runs WHERE run_id IN ({','.join('?' for _ in spec.data_center_run_ids)})",
                tuple(spec.data_center_run_ids),
            ).fetchall()
        found_versions = {str(run_id): str(version) for run_id, version in rows}
        if found_versions:
            actual_dataset_version = aggregate_dataset_version(
                [found_versions[run_id] for run_id in spec.data_center_run_ids if run_id in found_versions]
            )
            if actual_dataset_version != spec.dataset_version:
                events.append(self._event(
                    "DATASET_VERSION_MISMATCH",
                    ValidationStatus.BLOCKED,
                    "Data Center版本集合与ExperimentSpec.dataset_version不一致",
                    details={"expected": spec.dataset_version, "actual": actual_dataset_version},
                ))
        for run_id in spec.data_center_run_ids:
            self._validate_run(spec, run_id, events)
        return self._result(spec, events)

    def _validate_run(
        self, spec: ExperimentSpec, run_id: str, events: list[ValidationEvent]
    ) -> None:
        """Validate one catalog row, manifest and referenced immutable assets."""
        with sqlite3.connect(self.catalog) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM research_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            events.append(self._event("RUN_NOT_FOUND", ValidationStatus.BLOCKED,
                                      "Data Center run_id不存在", run_id))
            return
        if int(row["verified"]) != 1:
            events.append(self._event("RUN_UNVERIFIED", ValidationStatus.BLOCKED,
                                      "Data Center run尚未通过权威重放", run_id))
        for field, expected, code in (
            ("strategy_version", spec.strategy_version, "STRATEGY_VERSION_MISMATCH"),
            ("factor_version", spec.factor_version, "FACTOR_VERSION_MISMATCH"),
            ("factor_contract_hash", spec.factor_contract_hash, "FACTOR_HASH_MISMATCH"),
        ):
            if str(row[field]) != expected:
                events.append(self._event(code, ValidationStatus.BLOCKED,
                                          f"{field}与ExperimentSpec不一致", run_id,
                                          {"expected": expected, "actual": row[field]}))
        research_date = str(row["research_date"])
        if not spec.data_start <= research_date <= spec.data_end:
            events.append(self._event("DATE_OUT_OF_RANGE", ValidationStatus.BLOCKED,
                                      "研究日期超出实验数据区间", run_id,
                                      {"research_date": research_date}))
        manifest_path = (self.root / str(row["manifest_path"])).resolve()
        if self.root.resolve() not in manifest_path.parents or not manifest_path.is_file():
            events.append(self._event("MANIFEST_MISSING", ValidationStatus.BLOCKED,
                                      "manifest缺失或路径越界", run_id))
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            events.append(self._event("MANIFEST_INVALID", ValidationStatus.BLOCKED,
                                      "manifest无法读取", run_id, {"error": str(exc)[:200]}))
            return
        if manifest.get("run_id") != run_id or manifest.get("data_version") != row["data_version"]:
            events.append(self._event("MANIFEST_IDENTITY_MISMATCH", ValidationStatus.BLOCKED,
                                      "manifest身份或数据版本不一致", run_id))
        self._scan_secrets(manifest, events, run_id, "manifest")
        assets = manifest.get("assets") or {}
        required = {
            "security_universe_pit", "market_data_pit", "financial_point_in_time",
            "feature_store", "factor_store", "final_ranking", "portfolio_weights",
        }
        missing = sorted(required - set(assets))
        if missing:
            events.append(self._event("ASSETS_MISSING", ValidationStatus.BLOCKED,
                                      "Data Center证据资产不完整", run_id, {"missing": missing}))
            return
        decoded: dict[str, Any] = {}
        for name, asset in assets.items():
            content = self._asset_content(asset, events, run_id, name)
            if content is None:
                continue
            try:
                decoded[name] = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                events.append(self._event("ASSET_JSON_INVALID", ValidationStatus.BLOCKED,
                                          f"资产{name}不是有效JSON", run_id))
                continue
            self._scan_secrets(decoded[name], events, run_id, name)
        self._validate_relationships(spec, run_id, decoded, manifest, events)

    def _asset_content(
        self, asset: dict[str, Any], events: list[ValidationEvent], run_id: str, name: str
    ) -> bytes | None:
        """Read a content-addressed asset and verify path, size and SHA-256."""
        path = (self.root / str(asset.get("path") or "")).resolve()
        if self.root.resolve() not in path.parents or not path.is_file():
            events.append(self._event("ASSET_MISSING", ValidationStatus.BLOCKED,
                                      f"资产{name}缺失或路径越界", run_id))
            return None
        try:
            content = gzip.decompress(path.read_bytes())
        except (OSError, gzip.BadGzipFile) as exc:
            events.append(self._event("ASSET_INVALID", ValidationStatus.BLOCKED,
                                      f"资产{name}无法解压", run_id, {"error": str(exc)[:160]}))
            return None
        actual = hashlib.sha256(content).hexdigest()
        if actual != asset.get("sha256"):
            events.append(self._event("ASSET_HASH_MISMATCH", ValidationStatus.BLOCKED,
                                      f"资产{name}哈希不一致", run_id))
            return None
        return content

    def _validate_relationships(
        self,
        spec: ExperimentSpec,
        run_id: str,
        assets: dict[str, Any],
        manifest: dict[str, Any],
        events: list[ValidationEvent],
    ) -> None:
        """Check point-in-time semantics, primary keys, coverage and cross-asset references."""
        universe = list(assets.get("security_universe_pit") or [])
        market = list(assets.get("market_data_pit") or [])
        financial = list(assets.get("financial_point_in_time") or [])
        features = list(assets.get("feature_store") or [])
        ranking = list(assets.get("final_ranking") or [])
        portfolio = dict(assets.get("portfolio_weights") or {})
        research_date = date.fromisoformat(str(manifest["research_date"]))
        self._duplicates(universe, ("symbol",), "SECURITY_MASTER_PK", events, run_id)
        self._duplicates(market, ("symbol",), "MARKET_SNAPSHOT_PK", events, run_id)
        self._duplicates(financial, ("symbol", "available_at"), "FINANCIAL_PK", events, run_id)
        self._duplicates(features, ("symbol",), "FEATURE_PK", events, run_id)
        self._duplicates(ranking, ("symbol",), "RANKING_PK", events, run_id)
        for row in financial:
            available = str(row.get("available_at") or "")[:10]
            if not available:
                events.append(self._event("AVAILABLE_AT_MISSING", ValidationStatus.BLOCKED,
                                          "财务数据缺少available_at", run_id))
            elif available > research_date.isoformat():
                events.append(self._event("FUTURE_AVAILABLE_AT", ValidationStatus.BLOCKED,
                                          "财务数据在研究日后才可见", run_id,
                                          {"symbol": row.get("symbol"), "available_at": available}))
        for row in universe:
            listed = str(row.get("list_date") or "")[:10]
            delisted = str(row.get("delist_date") or "")[:10]
            if self.policy.require_listing_history and not listed:
                events.append(self._event("LISTING_HISTORY_UNAVAILABLE", ValidationStatus.BLOCKED,
                                          "证券主数据缺少list_date", run_id,
                                          {"symbol": row.get("symbol")}))
            if listed and listed > research_date.isoformat():
                events.append(self._event("PRE_LISTING_LEAK", ValidationStatus.BLOCKED,
                                          "证券在上市前进入股票池", run_id,
                                          {"symbol": row.get("symbol")}))
            if self.policy.require_delisting_history and "delist_date" not in row:
                events.append(self._event("DELISTING_HISTORY_UNAVAILABLE", ValidationStatus.BLOCKED,
                                          "证券主数据缺少delist_date字段", run_id))
            if delisted and delisted <= research_date.isoformat():
                events.append(self._event("POST_DELIST_LEAK", ValidationStatus.BLOCKED,
                                          "已退市证券进入研究股票池", run_id,
                                          {"symbol": row.get("symbol")}))
        universe_symbols = {str(row.get("symbol")) for row in universe}
        feature_symbols = {str(row.get("symbol")) for row in features}
        ranking_symbols = {str(row.get("symbol")) for row in ranking}
        targets = {str(item) for item in portfolio.get("targets", [])}
        if not feature_symbols.issubset(universe_symbols):
            events.append(self._event("FEATURE_UNIVERSE_MISMATCH", ValidationStatus.BLOCKED,
                                      "Feature Store包含股票池外证券", run_id))
        if not ranking_symbols.issubset(feature_symbols):
            events.append(self._event("RANKING_FEATURE_MISMATCH", ValidationStatus.BLOCKED,
                                      "排名包含Feature Store外证券", run_id))
        if not targets.issubset(ranking_symbols):
            events.append(self._event("PORTFOLIO_RANKING_MISMATCH", ValidationStatus.BLOCKED,
                                      "组合目标包含排名外证券", run_id))
        financial_symbols = {str(row.get("symbol")) for row in financial}
        coverage = len(feature_symbols & financial_symbols) / max(len(feature_symbols), 1)
        if coverage < self.policy.minimum_financial_coverage:
            events.append(self._event("FINANCIAL_COVERAGE_LOW", ValidationStatus.BLOCKED,
                                      "点时财务覆盖率低于实验门槛", run_id,
                                      {"coverage": coverage,
                                       "minimum": self.policy.minimum_financial_coverage}))
        self._availability_event(
            market, "suspended", self.policy.require_trading_status,
            "SUSPENSION_HISTORY", events, run_id,
        )
        limit_present = bool(market) and all(
            "upper_limit_price" in row and "lower_limit_price" in row for row in market
        )
        if not limit_present:
            events.append(self._event(
                "LIMIT_PRICE_HISTORY_UNAVAILABLE",
                ValidationStatus.BLOCKED if self.policy.require_limit_prices else ValidationStatus.UNAVAILABLE,
                "历史涨跌停价格证据不可用", run_id,
            ))
        corporate_present = bool(universe) and all("corporate_action_version" in row for row in universe)
        if not corporate_present:
            events.append(self._event(
                "CORPORATE_ACTION_HISTORY_UNAVAILABLE",
                ValidationStatus.BLOCKED if self.policy.require_corporate_actions else ValidationStatus.UNAVAILABLE,
                "公司行为历史证据不可用", run_id,
            ))
        quality = manifest.get("data_quality") or {}
        if float(quality.get("financial_coverage", coverage)) < self.policy.minimum_financial_coverage:
            events.append(self._event("MANIFEST_COVERAGE_LOW", ValidationStatus.BLOCKED,
                                      "manifest财务覆盖率低于门槛", run_id))
        events.append(self._event("RUN_EVIDENCE_VERIFIED", ValidationStatus.PASS,
                                  "Data Center manifest、资产哈希与引用关系已核验", run_id))

    def _availability_event(
        self,
        rows: list[dict[str, Any]],
        field: str,
        required: bool,
        label: str,
        events: list[ValidationEvent],
        run_id: str,
    ) -> None:
        """Record required or optional dataset capabilities without inventing coverage."""
        if not rows or not all(field in row for row in rows):
            events.append(self._event(
                f"{label}_UNAVAILABLE",
                ValidationStatus.BLOCKED if required else ValidationStatus.UNAVAILABLE,
                f"{label}字段不可用", run_id,
            ))

    def _duplicates(
        self,
        rows: Iterable[dict[str, Any]],
        fields: tuple[str, ...],
        code: str,
        events: list[ValidationEvent],
        run_id: str,
    ) -> None:
        """Detect duplicate logical primary keys in decoded Data Center assets."""
        keys = [tuple(row.get(field) for field in fields) for row in rows]
        if len(keys) != len(set(keys)):
            events.append(self._event(code, ValidationStatus.BLOCKED,
                                      f"数据主键重复：{fields}", run_id))

    def _scan_secrets(
        self, payload: Any, events: list[ValidationEvent], run_id: str, source: str
    ) -> None:
        """Fail closed if any persisted evidence key resembles a credential field."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                if _SECRET.search(str(key)):
                    events.append(self._event("SECRET_FIELD_DETECTED", ValidationStatus.BLOCKED,
                                              "实验数据包含疑似密钥字段", run_id,
                                              {"source": source, "field": str(key)}))
                self._scan_secrets(value, events, run_id, source)
        elif isinstance(payload, list):
            for item in payload:
                self._scan_secrets(item, events, run_id, source)

    def _result(self, spec: ExperimentSpec, events: list[ValidationEvent]) -> DatasetValidation:
        """Collapse event severities into one honest dataset decision."""
        statuses = {event.status for event in events}
        status = (
            ValidationStatus.BLOCKED if ValidationStatus.BLOCKED in statuses
            else ValidationStatus.PARTIAL if statuses & {ValidationStatus.PARTIAL, ValidationStatus.UNAVAILABLE}
            else ValidationStatus.PASS
        )
        return DatasetValidation(
            status=status,
            checked_at=utc_now(),
            events=tuple(events),
            dataset_version=spec.dataset_version,
            run_ids=spec.data_center_run_ids,
        )

    @staticmethod
    def _event(
        code: str,
        status: ValidationStatus,
        message: str,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ValidationEvent:
        """Create a normalized validation event."""
        return ValidationEvent(code, status, message, run_id, details or {})
