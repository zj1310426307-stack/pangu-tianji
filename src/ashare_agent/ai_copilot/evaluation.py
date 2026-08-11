from __future__ import annotations

import json
import re
from typing import Any

from ..core.contracts import CopilotEvidenceBundle


EVALUATION_VERSION = "copilot-evaluation-v1.0.0"
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")


class CopilotEvaluation:
    """Validate response shape, citations and numeric grounding before publication."""

    def evaluate(
        self,
        content: dict[str, Any],
        bundle: CopilotEvidenceBundle,
        *,
        allow_memory: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Normalize bounded output and return an auditable grounding verdict."""
        schema_errors: list[str] = []
        if not isinstance(content, dict):
            content = {}
            schema_errors.append("模型输出不是JSON对象")
        headline = self._text(content.get("headline"), 120, "headline", schema_errors)
        summary = self._text(content.get("summary"), 4000, "summary", schema_errors)
        disclaimer = self._text(
            content.get("disclaimer"), 300, "disclaimer", schema_errors
        )
        allowed_ids = {item.evidence_id for item in bundle.evidence_items}
        citation_total = 0
        citation_valid = 0
        unknown_citations: set[str] = set()
        missing_citation_count = 0

        findings, stats = self._entries(
            content.get("findings"),
            limit=8,
            allowed_ids=allowed_ids,
            fields=("title", "detail"),
            schema_errors=schema_errors,
        )
        citation_total += stats[0]
        citation_valid += stats[1]
        unknown_citations.update(stats[2])
        missing_citation_count += stats[3]
        risks, stats = self._entries(
            content.get("risks"),
            limit=6,
            allowed_ids=allowed_ids,
            fields=("level", "message"),
            schema_errors=schema_errors,
            allowed_levels={"info", "warning", "high"},
        )
        citation_total += stats[0]
        citation_valid += stats[1]
        unknown_citations.update(stats[2])
        missing_citation_count += stats[3]
        memories, stats = self._memory_entries(
            content.get("memory_candidates"),
            allowed_ids,
            schema_errors,
        )
        citation_total += stats[0]
        citation_valid += stats[1]
        unknown_citations.update(stats[2])
        missing_citation_count += stats[3]
        if memories and not allow_memory:
            schema_errors.append("非Coach Agent不得生成投资记忆")
            memories = []

        normalized = {
            "headline": headline,
            "summary": summary,
            "findings": findings,
            "risks": risks,
            "memory_candidates": memories,
            "disclaimer": disclaimer,
            "can_trade": False,
            "used_for_execution": False,
        }
        allowed_numbers = self._allowed_numbers(bundle)
        numeric_claims = self._claim_numbers(normalized)
        numeric_errors = sorted(
            number for number in numeric_claims if self._canonical(number) not in allowed_numbers
        )
        grounded = not (
            schema_errors
            or unknown_citations
            or missing_citation_count
            or numeric_errors
        )
        evaluation = {
            "evaluation_version": EVALUATION_VERSION,
            "grounded": grounded,
            "citation_accuracy": (
                round(citation_valid / citation_total, 6) if citation_total else 0.0
            ),
            "citation_count": citation_total,
            "valid_citation_count": citation_valid,
            "unknown_citations": sorted(unknown_citations),
            "missing_citation_count": missing_citation_count,
            "numeric_claim_count": len(numeric_claims),
            "numeric_errors": numeric_errors,
            "schema_errors": schema_errors,
            "human_rating": None,
        }
        return normalized, evaluation

    @staticmethod
    def _text(value: Any, limit: int, field: str, errors: list[str]) -> str:
        """Validate one required bounded string."""
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field}缺失或格式无效")
            return ""
        return value.strip()[:limit]

    def _entries(
        self,
        raw: Any,
        *,
        limit: int,
        allowed_ids: set[str],
        fields: tuple[str, str],
        schema_errors: list[str],
        allowed_levels: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], tuple[int, int, set[str], int]]:
        """Normalize cited findings or risk entries and count citation quality."""
        if not isinstance(raw, list):
            schema_errors.append(f"{fields[0]}列表格式无效")
            raw = []
        result: list[dict[str, Any]] = []
        total = valid = missing = 0
        unknown: set[str] = set()
        for item in raw[:limit]:
            if not isinstance(item, dict):
                schema_errors.append(f"{fields[0]}条目格式无效")
                continue
            first = str(item.get(fields[0]) or "").strip()[:160]
            second = str(item.get(fields[1]) or "").strip()[:1600]
            if not first or not second:
                schema_errors.append(f"{fields[0]}/{fields[1]}不得为空")
            if allowed_levels is not None and first not in allowed_levels:
                schema_errors.append("风险等级无效")
            citations = item.get("evidence_ids")
            if not isinstance(citations, list) or not citations:
                citations = []
                missing += 1
            cleaned: list[str] = []
            for citation in citations[:12]:
                citation_id = str(citation)
                total += 1
                if citation_id in allowed_ids:
                    valid += 1
                    cleaned.append(citation_id)
                else:
                    unknown.add(citation_id)
            result.append(
                {fields[0]: first, fields[1]: second, "evidence_ids": cleaned}
            )
        return result, (total, valid, unknown, missing)

    def _memory_entries(
        self,
        raw: Any,
        allowed_ids: set[str],
        schema_errors: list[str],
    ) -> tuple[list[dict[str, Any]], tuple[int, int, set[str], int]]:
        """Normalize evidence-cited, unconfirmed memory candidates."""
        if not isinstance(raw, list):
            schema_errors.append("memory_candidates格式无效")
            raw = []
        categories = {"preference", "decision", "error_pattern", "lesson"}
        result: list[dict[str, Any]] = []
        total = valid = missing = 0
        unknown: set[str] = set()
        for item in raw[:3]:
            if not isinstance(item, dict):
                schema_errors.append("memory_candidate格式无效")
                continue
            category = str(item.get("category") or "")
            content = str(item.get("content") or "").strip()[:1000]
            try:
                confidence = max(0.0, min(float(item.get("confidence")), 0.6))
            except (TypeError, ValueError):
                confidence = 0.0
                schema_errors.append("memory_candidate.confidence无效")
            if category not in categories or not content:
                schema_errors.append("memory_candidate分类或内容无效")
            citations = item.get("evidence_ids")
            if not isinstance(citations, list) or not citations:
                citations = []
                missing += 1
            cleaned: list[str] = []
            for citation in citations[:12]:
                citation_id = str(citation)
                total += 1
                if citation_id in allowed_ids:
                    valid += 1
                    cleaned.append(citation_id)
                else:
                    unknown.add(citation_id)
            result.append(
                {
                    "category": category,
                    "content": content,
                    "confidence": confidence,
                    "evidence_ids": cleaned,
                }
            )
        return result, (total, valid, unknown, missing)

    @staticmethod
    def _allowed_numbers(bundle: CopilotEvidenceBundle) -> set[str]:
        """Build exact and rounded representations for evidence-backed numbers."""
        allowed: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, bool) or value is None:
                return
            elif isinstance(value, (int, float)):
                number = float(value)
                for digits in range(5):
                    allowed.add(CopilotEvaluation._canonical(f"{number:.{digits}f}"))
                if abs(number) <= 1:
                    for digits in range(4):
                        allowed.add(
                            CopilotEvaluation._canonical(
                                f"{number * 100:.{digits}f}%"
                            )
                        )
            elif isinstance(value, str):
                for token in _NUMBER_PATTERN.findall(value):
                    allowed.add(CopilotEvaluation._canonical(token))

        for evidence in bundle.evidence_items:
            walk(evidence.payload)
            walk(evidence.observed_at)
            walk(evidence.evidence_id)
        walk(bundle.research_date)
        return allowed

    @staticmethod
    def _claim_numbers(content: dict[str, Any]) -> set[str]:
        """Extract human-facing numeric claims while ignoring confidence metadata."""
        texts: list[str] = [
            str(content.get("headline") or ""),
            str(content.get("summary") or ""),
        ]
        for item in content.get("findings", []):
            texts.extend([str(item.get("title") or ""), str(item.get("detail") or "")])
        for item in content.get("risks", []):
            texts.append(str(item.get("message") or ""))
        for item in content.get("memory_candidates", []):
            texts.append(str(item.get("content") or ""))
        return {token for text in texts for token in _NUMBER_PATTERN.findall(text)}

    @staticmethod
    def _canonical(value: str) -> str:
        """Normalize separators, plus signs and redundant decimal zeroes."""
        raw = value.replace(",", "").strip()
        percent = raw.endswith("%")
        if percent:
            raw = raw[:-1]
        if raw.startswith("+"):
            raw = raw[1:]
        try:
            number = float(raw)
        except ValueError:
            return value
        normalized = f"{number:.8f}".rstrip("0").rstrip(".")
        return normalized + ("%" if percent else "")
