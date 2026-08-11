from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from ..core.contracts import CopilotEvidenceBundle, CopilotEvidenceItem
from ..data_center import DataCenter, DataCenterError


EVIDENCE_READER_VERSION = "evidence-reader-v1.0.0"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,180}$")
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_SECRET_MARKERS = ("api_key", "password", "passwd", "token", "secret", "credential")


class EvidenceReaderError(RuntimeError):
    """Reject missing, corrupt, ungrounded, or over-broad Copilot evidence."""


class EvidenceReader:
    """Read bounded evidence so AI agents never receive database access objects."""

    SCOPES = {
        "morning_report",
        "close_review",
        "stock_analysis",
        "portfolio_analysis",
        "risk_alert",
        "coach_review",
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.output_dir = self.project_root / "output"
        self.data_center_root = self.output_dir / "data_center"
        self.portfolio_db = self.output_dir / "portfolio_risk_center.db"

    def _open_data_center(self) -> DataCenter:
        """Open an existing center lazily so status reads create no data assets."""
        if not (self.data_center_root / "catalog.sqlite3").is_file():
            raise EvidenceReaderError("请先完成一次Data Center正式研究")
        return DataCenter(self.data_center_root)

    def latest_run_id(self) -> str | None:
        """Return the latest Data Center run id without falling back to stale UI files."""
        path = self.output_dir / "data_center" / "latest.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        run_id = str(payload.get("run_id") or "")
        return run_id if _RUN_ID_PATTERN.fullmatch(run_id) else None

    def get_stock_evidence(
        self,
        symbol: str,
        run_id: str | None = None,
    ) -> CopilotEvidenceBundle:
        """Return bounded saved evidence for one ranked stock, never a DB handle."""
        return self.read(run_id, scope="stock_analysis", symbol=symbol)

    def get_portfolio_evidence(
        self,
        run_id: str | None = None,
    ) -> CopilotEvidenceBundle:
        """Return the saved target portfolio and its source-owned evidence."""
        return self.read(run_id, scope="portfolio_analysis")

    def get_risk_evidence(
        self,
        run_id: str | None = None,
    ) -> CopilotEvidenceBundle:
        """Return saved risk snapshots and exit signals without execution access."""
        return self.read(run_id, scope="risk_alert")

    def get_review_history(
        self,
        run_id: str | None = None,
    ) -> CopilotEvidenceBundle:
        """Return review evidence for one saved run; it does not infer raw history."""
        return self.read(run_id, scope="close_review")

    def read(
        self,
        run_id: str | None = None,
        *,
        scope: str,
        symbol: str | None = None,
    ) -> CopilotEvidenceBundle:
        """Build one checksum-backed bundle from Data and Portfolio Risk Centers."""
        if scope not in self.SCOPES:
            raise EvidenceReaderError("不支持的AI证据范围")
        selected_run = str(run_id or self.latest_run_id() or "")
        if not _RUN_ID_PATTERN.fullmatch(selected_run):
            raise EvidenceReaderError("请先完成一次Data Center正式研究")
        normalized_symbol = symbol.strip().upper() if symbol else None
        if normalized_symbol and not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise EvidenceReaderError("股票代码格式无效")
        if scope == "stock_analysis" and not normalized_symbol:
            raise EvidenceReaderError("股票分析必须指定股票代码")

        try:
            data_center = self._open_data_center()
            manifest = data_center.manifest(selected_run)
            ranking = data_center.read_asset(selected_run, "final_ranking")
            factors = data_center.read_asset(selected_run, "factor_store")
            upstream_weights = data_center.read_asset(
                selected_run, "portfolio_weights"
            )
        except (DataCenterError, EvidenceReaderError) as exc:
            raise EvidenceReaderError(str(exc)) from exc
        if not manifest.get("quality_checks", {}).get("sensitive_fields_absent"):
            raise EvidenceReaderError("Data Center未通过敏感字段质量检查")
        if not isinstance(ranking, list) or not isinstance(factors, list):
            raise EvidenceReaderError("Data Center排名或因子资产结构无效")

        portfolio = self._read_portfolio_center(selected_run)
        if portfolio is None:
            raise EvidenceReaderError(
                "Portfolio Risk Center缺少该run_id的组合证据"
            )
        items: list[CopilotEvidenceItem] = [
            self._item(
                "E-DC-MANIFEST",
                "data_center",
                manifest.get("data_time"),
                {
                    "run_id": manifest.get("run_id"),
                    "run_kind": manifest.get("run_kind"),
                    "research_date": manifest.get("research_date"),
                    "created_at": manifest.get("created_at"),
                    "strategy_version": manifest.get("strategy_version"),
                    "factor_version": manifest.get("factor_version"),
                    "factor_contract_hash": manifest.get("factor_contract_hash"),
                    "data_version": manifest.get("data_version"),
                    "data_model_version": manifest.get("data_model_version"),
                    "data_source": manifest.get("data_source"),
                    "data_time": manifest.get("data_time"),
                    "stage_counts": manifest.get("stage_counts", {}),
                    "data_quality": manifest.get("data_quality", {}),
                },
            ),
            self._item(
                "E-DC-UPSTREAM-PORTFOLIO",
                "data_center",
                manifest.get("data_time"),
                dict(upstream_weights or {}),
            ),
        ]

        factor_by_symbol = {
            str(row.get("symbol")): row for row in factors if isinstance(row, dict)
        }
        ranked_rows = [row for row in ranking if isinstance(row, dict)]
        if normalized_symbol:
            ranked_rows = [
                row for row in ranked_rows if str(row.get("symbol")) == normalized_symbol
            ]
            if not ranked_rows:
                raise EvidenceReaderError("该股票不在指定run_id的最终排名中")
        else:
            ranked_rows = sorted(
                ranked_rows,
                key=lambda row: int(row.get("rank") or 999999),
            )[:10]
        for row in ranked_rows:
            row_symbol = str(row.get("symbol"))
            items.append(
                self._item(
                    f"E-DC-RANK-{row_symbol}",
                    "data_center",
                    manifest.get("data_time"),
                    self._ranking_payload(row),
                )
            )
            factor = factor_by_symbol.get(row_symbol)
            if factor:
                items.append(
                    self._item(
                        f"E-DC-FACTOR-{row_symbol}",
                        "data_center",
                        manifest.get("data_time"),
                        self._factor_payload(factor),
                    )
                )

        profile = portfolio["profile"]
        items.append(
            self._item(
                "E-PR-PROFILE",
                "portfolio_risk_center",
                profile.get("created_at"),
                profile,
            )
        )
        items.append(
            self._item(
                "E-PR-PORTFOLIO",
                "portfolio_risk_center",
                portfolio["portfolio"].get("created_at"),
                portfolio["portfolio"],
            )
        )
        for position in portfolio["positions"]:
            position_symbol = str(position.get("symbol"))
            if normalized_symbol and position_symbol != normalized_symbol:
                continue
            items.append(
                self._item(
                    f"E-PR-POSITION-{position_symbol}",
                    "portfolio_risk_center",
                    portfolio["portfolio"].get("created_at"),
                    position,
                )
            )
        for risk in portfolio["risk_snapshots"]:
            suffix = str(risk.get("snapshot_type") or "unknown").upper()
            items.append(
                self._item(
                    f"E-PR-RISK-{suffix}",
                    "portfolio_risk_center",
                    risk.get("observed_at"),
                    risk,
                )
            )
        for signal in portfolio["exit_signals"]:
            signal_symbol = str(signal.get("symbol"))
            if normalized_symbol and signal_symbol != normalized_symbol:
                continue
            items.append(
                self._item(
                    f"E-PR-EXIT-{signal_symbol}",
                    "portfolio_risk_center",
                    signal.get("observed_at"),
                    signal,
                )
            )

        data_gaps: list[str] = []
        if not any(
            item.evidence_id.startswith("E-PR-RISK-")
            and item.payload.get("snapshot_type") != "planned"
            for item in items
        ):
            data_gaps.append(
                "尚无ValuationService支持的账户风险快照，不得描述实际盈亏"
            )
        if any(
            not item.payload.get("industry")
            for item in items
            if item.evidence_id.startswith("E-DC-RANK-")
        ):
            data_gaps.append("个股行业字段未完整覆盖")

        canonical = {
            "run_id": selected_run,
            "scope": scope,
            "symbol": normalized_symbol,
            "items": [asdict(item) for item in items],
            "data_gaps": data_gaps,
        }
        evidence_hash = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return CopilotEvidenceBundle(
            run_id=selected_run,
            research_date=str(manifest.get("research_date") or ""),
            strategy_version=str(manifest.get("strategy_version") or ""),
            factor_version=str(manifest.get("factor_version") or ""),
            data_version=str(manifest.get("data_version") or ""),
            evidence_hash=evidence_hash,
            evidence_items=items,
            data_gaps=data_gaps,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def public_payload(self, bundle: CopilotEvidenceBundle) -> dict[str, Any]:
        """Serialize a bundle without leaking internal paths or database handles."""
        payload = asdict(bundle)
        payload["reader_version"] = EVIDENCE_READER_VERSION
        payload["can_trade"] = False
        payload["used_for_execution"] = False
        return payload

    def _read_portfolio_center(self, run_id: str) -> dict[str, Any] | None:
        """Read Portfolio Risk Center through a read-only, integrity-checked channel."""
        if not self.portfolio_db.exists():
            return None
        uri = f"file:{self.portfolio_db.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            integrity = connection.execute("PRAGMA integrity_check(1)").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise EvidenceReaderError("Portfolio Risk Center完整性检查失败")
            portfolio_row = connection.execute(
                "SELECT * FROM portfolios WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if portfolio_row is None:
                return None
            portfolio = dict(portfolio_row)
            if int(portfolio.get("execution_authorized") or 0) != 0:
                raise EvidenceReaderError("组合证据含非法执行授权")
            profile_row = connection.execute(
                "SELECT * FROM investment_profiles WHERE profile_id=?",
                (portfolio["profile_id"],),
            ).fetchone()
            if profile_row is None:
                raise EvidenceReaderError("组合证据缺少Investment Profile")
            positions = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM portfolio_positions WHERE portfolio_id=? ORDER BY target_weight DESC,symbol",
                    (portfolio["portfolio_id"],),
                ).fetchall()
            ]
            risk_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM risk_snapshots WHERE portfolio_id=? ORDER BY observed_at DESC,snapshot_id DESC",
                    (portfolio["portfolio_id"],),
                ).fetchall()
            ]
            latest_by_type: dict[str, dict[str, Any]] = {}
            for row in risk_rows:
                latest_by_type.setdefault(str(row["snapshot_type"]), row)
            selected_risks = [latest_by_type[key] for key in sorted(latest_by_type)]
            for row in selected_risks:
                for field in (
                    "industry_exposure_json",
                    "style_exposure_json",
                    "size_exposure_json",
                    "cycle_exposure_json",
                    "concentration_json",
                    "risk_flags_json",
                ):
                    row[field.removesuffix("_json")] = self._parse_json(row.pop(field))
            snapshot_ids = [row["snapshot_id"] for row in selected_risks]
            exits: list[dict[str, Any]] = []
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                rows = connection.execute(
                    f"""SELECT exit_signals.*,risk_snapshots.observed_at
                    FROM exit_signals JOIN risk_snapshots USING(snapshot_id)
                    WHERE exit_signals.snapshot_id IN ({placeholders})
                    ORDER BY risk_snapshots.observed_at DESC,exit_signals.symbol""",
                    snapshot_ids,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    if int(item.get("creates_order") or 0) != 0:
                        raise EvidenceReaderError("退出证据含非法订单标记")
                    item["reasons"] = self._parse_json(item.pop("reasons_json"))
                    exits.append(item)
            return {
                "portfolio": portfolio,
                "profile": dict(profile_row),
                "positions": positions,
                "risk_snapshots": selected_risks,
                "exit_signals": exits,
            }
        except sqlite3.Error as exc:
            raise EvidenceReaderError("Portfolio Risk Center读取失败") from exc
        finally:
            connection.close()

    @staticmethod
    def _parse_json(value: Any) -> Any:
        """Parse one persisted JSON field and reject malformed evidence."""
        try:
            return json.loads(str(value))
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvidenceReaderError("组合风险JSON证据无效") from exc

    @staticmethod
    def _item(
        evidence_id: str,
        source: str,
        observed_at: Any,
        payload: dict[str, Any],
    ) -> CopilotEvidenceItem:
        """Create one secret-filtered evidence item with a stable citation id."""
        return CopilotEvidenceItem(
            evidence_id=evidence_id,
            source=source,
            observed_at=None if observed_at is None else str(observed_at),
            payload=EvidenceReader._redact(payload),
        )

    @staticmethod
    def _redact(value: Any) -> Any:
        """Remove secret-like keys recursively before model serialization."""
        if isinstance(value, dict):
            return {
                str(key): EvidenceReader._redact(item)
                for key, item in value.items()
                if not any(marker in str(key).lower() for marker in _SECRET_MARKERS)
            }
        if isinstance(value, list):
            return [EvidenceReader._redact(item) for item in value]
        if isinstance(value, float):
            if value != value or value in {float("inf"), float("-inf")}:
                return None
        return value

    @staticmethod
    def _ranking_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Select only explanatory ranking fields from the full factor row."""
        allowed = {
            "rank",
            "symbol",
            "name",
            "industry",
            "score",
            "last_price",
            "turnover",
            "market_cap",
            "momentum",
            "trend_strength",
            "volatility",
            "max_drawdown",
            "ma20",
            "ma60",
            "profit_growth",
            "revenue_growth",
            "roe",
            "cash_quality",
            "valuation_complete",
            "points_value",
            "points_quality",
            "points_growth",
            "points_momentum",
            "points_trend",
            "points_low_risk",
            "points_liquidity",
        }
        return {key: row.get(key) for key in sorted(allowed) if key in row}

    @staticmethod
    def _factor_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Select versioned factor evidence without sending raw market history."""
        allowed = {
            "symbol",
            "score",
            "points_value",
            "points_quality",
            "points_growth",
            "points_momentum",
            "points_trend",
            "points_low_risk",
            "points_liquidity",
            "valuation_complete",
        }
        return {key: row.get(key) for key in sorted(allowed) if key in row}
