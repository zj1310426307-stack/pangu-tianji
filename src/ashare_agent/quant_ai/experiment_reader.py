from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Any, Mapping

from ..ai_copilot.evidence_reader import EvidenceReader, EvidenceReaderError
from ..quant_lab.contracts import sha256_json
from ..quant_lab.experiment_registry import ExperimentRegistry
from ..quant_lab.validation_gate.strategy_registry import StrategyRegistry
from .contracts import ResearchEvidenceBundle, ResearchEvidenceItem


EXPERIMENT_READER_VERSION = "quant-ai-experiment-reader-v1.0.0"
_FACTOR_ID = re.compile(r"^factor_report:([^:]+):[0-9a-f]{64}$")
_ROBUST_ID = re.compile(r"^robustness_report:([^:]+):[0-9a-f]{64}$")
_SECRET_KEYS = ("api_key", "password", "secret", "token", "credential")


class QuantAIExperimentReader:
    """Read only sealed research evidence and expose no experiment mutation methods."""

    def __init__(
        self,
        project_root: Path,
        *,
        strategy_registry: StrategyRegistry | None = None,
        experiment_registry: ExperimentRegistry | None = None,
        portfolio_reader: EvidenceReader | None = None,
    ) -> None:
        self.root = Path(project_root)
        self.output = self.root / "output"
        self.strategy_registry = strategy_registry or StrategyRegistry(
            self.output / "agent.db"
        )
        self.experiment_registry = experiment_registry
        self.portfolio_reader = portfolio_reader or EvidenceReader(self.root)

    def latest(self) -> ResearchEvidenceBundle:
        """Return the latest validation-bound bundle or an honest empty snapshot."""
        reviews = self.strategy_registry.list_reviews(limit=1)
        if not reviews:
            return ResearchEvidenceBundle(
                strategy_id=None,
                strategy_version=None,
                experiment_id=None,
                research_run_id=None,
                dataset_label=None,
                items=(),
                data_gaps=("strategy_validation_report_unavailable",),
            )
        return self.read(str(reviews[0]["review_id"]))

    def read(self, review_id: str) -> ResearchEvidenceBundle:
        """Verify one committee review and every linked Quant Lab report by hash."""
        review = self.strategy_registry.review(review_id)
        version = self.strategy_registry.version(
            str(review["strategy_id"]), str(review["strategy_version"])
        )
        spec = dict(version["spec"])
        items: list[ResearchEvidenceItem] = []
        gaps: list[str] = []
        self._append(
            items,
            evidence_id=f"E-QA-VALIDATION-{review_id}",
            source_type="strategy_validation_report",
            source_id=review_id,
            observed_at=str(review.get("created_at") or "") or None,
            payload=review["report"],
        )

        factor_link, robust_link = self._linked_runs(review.get("evidence_ids") or [])
        registry = self._experiment_registry()
        factor_report = None
        robustness_report = None
        if factor_link and registry:
            factor_run_id, expected_factor_hash = factor_link
            try:
                factor_report = registry.factor_report(factor_run_id)
                if sha256_json(factor_report) != expected_factor_hash:
                    raise ValueError("factor_report_link_hash_mismatch")
                self._append(
                    items,
                    evidence_id=f"E-QA-FACTOR-{factor_run_id}",
                    source_type="factor_research_report",
                    source_id=factor_run_id,
                    observed_at=factor_report.get("generated_at"),
                    payload=factor_report,
                )
            except Exception:
                gaps.append("factor_report_hash_or_read_failure")
        else:
            gaps.append("factor_report_unavailable")
        if robust_link and registry:
            robust_run_id, expected_robust_hash = robust_link
            try:
                robustness_report = registry.robustness_report(robust_run_id)
                if sha256_json(robustness_report) != expected_robust_hash:
                    raise ValueError("robustness_report_link_hash_mismatch")
                self._append(
                    items,
                    evidence_id=f"E-QA-ROBUST-{robust_run_id}",
                    source_type="strategy_robustness_report",
                    source_id=robust_run_id,
                    observed_at=robustness_report.get("generated_at"),
                    payload=robustness_report,
                )
            except Exception:
                gaps.append("robustness_report_hash_or_read_failure")
        else:
            gaps.append("robustness_report_unavailable")

        market_payload = self._market_payload(factor_report, robustness_report)
        if market_payload:
            self._append(
                items,
                evidence_id=f"E-QA-MARKET-{review_id}",
                source_type="registered_market_regime_evidence",
                source_id=review_id,
                observed_at=review.get("created_at"),
                payload=market_payload,
            )
        else:
            gaps.append("formal_market_regime_evidence_unavailable")

        research_run_id = str(spec.get("research_run_id") or "") or None
        if research_run_id:
            try:
                risk_bundle = self.portfolio_reader.get_risk_evidence(research_run_id)
                risk_payload = self.portfolio_reader.public_payload(risk_bundle)
                self._append(
                    items,
                    evidence_id=f"E-QA-RISK-{research_run_id}",
                    source_type="portfolio_risk_evidence_bundle",
                    source_id=research_run_id,
                    observed_at=risk_payload.get("generated_at"),
                    payload=risk_payload,
                )
                gaps.extend(f"portfolio:{value}" for value in risk_bundle.data_gaps)
            except (EvidenceReaderError, OSError, ValueError):
                gaps.append("portfolio_risk_report_unavailable")
        else:
            gaps.append("portfolio_risk_report_unavailable")

        return ResearchEvidenceBundle(
            strategy_id=str(review["strategy_id"]),
            strategy_version=str(review["strategy_version"]),
            experiment_id=str(spec.get("research_run_id") or "") or None,
            research_run_id=research_run_id,
            dataset_label=str(review.get("dataset_label") or "") or None,
            items=tuple(items),
            data_gaps=tuple(dict.fromkeys(gaps)),
            reader_version=EXPERIMENT_READER_VERSION,
        )

    def _experiment_registry(self) -> ExperimentRegistry | None:
        """Open the existing registry only when the immutable database already exists."""
        if self.experiment_registry is not None:
            return self.experiment_registry
        path = self.output / "quant_lab" / "experiments.sqlite3"
        if not path.is_file():
            return None
        self.experiment_registry = ExperimentRegistry(path)
        return self.experiment_registry

    @staticmethod
    def _linked_runs(
        evidence_ids: list[str],
    ) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
        """Extract linked run identities and expected hashes from the gate manifest."""
        factor = robust = None
        for value in evidence_ids:
            factor_match = _FACTOR_ID.match(str(value))
            robust_match = _ROBUST_ID.match(str(value))
            if factor_match:
                factor = (factor_match.group(1), str(value).rsplit(":", 1)[-1])
            if robust_match:
                robust = (robust_match.group(1), str(value).rsplit(":", 1)[-1])
        return factor, robust

    @staticmethod
    def _market_payload(
        factor_report: Mapping[str, Any] | None,
        robustness_report: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Expose only registered regime evidence; never infer a market label here."""
        factor_regime = ((factor_report or {}).get("sections") or {}).get("08_market_regime")
        robust_regime = ((robustness_report or {}).get("sections") or {}).get("07_market_regime")
        if not factor_regime and not robust_regime:
            return None
        return {
            "factor_regime": factor_regime,
            "robustness_regime": robust_regime,
            "historical_analogy_generated": False,
            "market_label_inferred": False,
        }

    @classmethod
    def _append(
        cls,
        items: list[ResearchEvidenceItem],
        *,
        evidence_id: str,
        source_type: str,
        source_id: str,
        observed_at: Any,
        payload: Mapping[str, Any],
    ) -> None:
        """Redact secret-like keys before hashing and entering the model boundary."""
        safe = cls._redact(dict(payload))
        items.append(ResearchEvidenceItem(
            evidence_id=evidence_id,
            source_type=source_type,
            source_id=source_id,
            source_hash=sha256_json(safe),
            observed_at=str(observed_at) if observed_at else None,
            payload=safe,
        ))

    @classmethod
    def _redact(cls, value: Any) -> Any:
        """Recursively remove credential-shaped fields from research evidence."""
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "[REDACTED]"
                    if any(token in str(key).lower() for token in _SECRET_KEYS)
                    else cls._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._redact(item) for item in value]
        return value
