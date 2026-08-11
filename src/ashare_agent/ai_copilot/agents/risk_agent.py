from ...core.contracts import CopilotAgentType, CopilotReportType
from .base import BaseCopilotAgent


class RiskAgent(BaseCopilotAgent):
    """Explain persisted security and portfolio risk signals without overrides."""

    agent_type = CopilotAgentType.RISK
    supported_reports = frozenset({CopilotReportType.RISK_ALERT})

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Focus the report on risk flags, exposure, drawdown and data gaps."""
        return "扫描已保存风险快照，按严重性解释个股风险、压力回撤代理、集中度和各类暴露。"
