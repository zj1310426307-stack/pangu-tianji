from ...core.contracts import CopilotAgentType, CopilotReportType
from .base import BaseCopilotAgent


class PortfolioAgent(BaseCopilotAgent):
    """Explain stored target weights and sizing coefficients without recalculation."""

    agent_type = CopilotAgentType.PORTFOLIO
    supported_reports = frozenset({CopilotReportType.PORTFOLIO_ANALYSIS})

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Describe why the stored target portfolio has its recorded shape."""
        return "解释已保存的目标权重、仓位系数、暴露、现金储备和当前/目标差异，禁止重算权重。"
