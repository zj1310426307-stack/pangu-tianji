from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .contracts import ResearchEvidenceBundle


QUANT_AI_EVALUATION_VERSION = "quant-ai-evaluation-v1.0.0"
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")


class QuantAIResearchEvaluation:
    """Fail closed on missing citations, invented numbers and unlabelled reasoning."""

    def evaluate_claims(
        self,
        claims: Iterable[Mapping[str, Any]],
        bundle: ResearchEvidenceBundle,
    ) -> dict[str, Any]:
        """Validate structured claims against the exact evidence supplied to the model."""
        known_ids = {item.evidence_id for item in bundle.items}
        allowed_numbers = self._numbers([item.payload for item in bundle.items])
        unknown_citations: set[str] = set()
        missing_citations: list[str] = []
        invalid_labels: list[str] = []
        numeric_errors: set[str] = set()
        claim_count = 0
        for index, claim in enumerate(claims):
            claim_count += 1
            claim_id = str(claim.get("claim_id") or f"claim-{index + 1}")
            citations = [str(value) for value in claim.get("evidence_ids") or []]
            if not citations:
                missing_citations.append(claim_id)
            unknown_citations.update(set(citations) - known_ids)
            if str(claim.get("label") or "") not in {"FACT", "INFERENCE", "HYPOTHESIS"}:
                invalid_labels.append(claim_id)
            claimed_numbers = self._numbers([
                claim.get("statement", ""), claim.get("metrics", {}), claim.get("title", "")
            ])
            numeric_errors.update(claimed_numbers - allowed_numbers)
        grounded = not any((
            unknown_citations, missing_citations, invalid_labels, numeric_errors
        ))
        return {
            "evaluation_version": QUANT_AI_EVALUATION_VERSION,
            "grounded": grounded,
            "claim_count": claim_count,
            "fact_consistency": not numeric_errors,
            "citation_complete": not missing_citations and not unknown_citations,
            "reasoning_labels_complete": not invalid_labels,
            "unknown_citations": sorted(unknown_citations),
            "missing_citations": missing_citations,
            "invalid_labels": invalid_labels,
            "numeric_errors": sorted(numeric_errors),
            "evidence_hash": bundle.evidence_hash,
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
        }

    @classmethod
    def _numbers(cls, values: Iterable[Any]) -> set[str]:
        """Normalize every finite number found in nested evidence or claims."""
        found: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, bool) or value is None:
                return
            if isinstance(value, (int, float)):
                found.add(cls._canonical(str(value)))
                return
            if isinstance(value, Mapping):
                for key, item in value.items():
                    visit(key)
                    visit(item)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    visit(item)
                return
            for number in _NUMBER.findall(str(value)):
                found.add(cls._canonical(number))

        for value in values:
            visit(value)
        return found

    @staticmethod
    def _canonical(value: str) -> str:
        """Canonicalize numeric strings so 1, 1.0 and +1 compare consistently."""
        percent = value.endswith("%")
        raw = value[:-1] if percent else value
        try:
            normalized = format(float(raw), ".12g")
        except ValueError:
            return value
        return f"{normalized}%" if percent else normalized
