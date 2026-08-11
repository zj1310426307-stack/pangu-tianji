from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ...core.contracts import (
    CopilotAgentType,
    CopilotEvidenceBundle,
    CopilotReportType,
)


class BaseCopilotAgent:
    """Shape one bounded task; model calls remain owned by CopilotService."""

    agent_type: CopilotAgentType
    supported_reports: frozenset[CopilotReportType]

    def build_payload(
        self,
        bundle: CopilotEvidenceBundle,
        report_type: CopilotReportType,
        *,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Serialize cited evidence and explicit non-execution boundaries."""
        if report_type not in self.supported_reports:
            raise ValueError("该Agent不支持所选报告类型")
        return {
            "task": {
                "agent_type": self.agent_type.value,
                "report_type": report_type.value,
                "symbol": symbol,
                "instructions": self.task_instructions(report_type),
            },
            "run": {
                "run_id": bundle.run_id,
                "research_date": bundle.research_date,
                "strategy_version": bundle.strategy_version,
                "factor_version": bundle.factor_version,
                "data_version": bundle.data_version,
                "evidence_hash": bundle.evidence_hash,
            },
            "evidence_items": [asdict(item) for item in bundle.evidence_items],
            "data_gaps": bundle.data_gaps,
            "safety": {
                "can_trade": False,
                "used_for_execution": False,
                "may_modify_score": False,
                "may_modify_portfolio": False,
                "may_modify_risk": False,
                "may_create_orders": False,
            },
        }

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Describe the report-specific question without adding market facts."""
        raise NotImplementedError
