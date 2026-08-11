from ...core.contracts import CopilotAgentType, CopilotReportType
from .base import BaseCopilotAgent


class ReviewAgent(BaseCopilotAgent):
    """Recap account-risk and exit evidence without inventing PnL facts."""

    agent_type = CopilotAgentType.REVIEW
    supported_reports = frozenset({CopilotReportType.CLOSE_REVIEW})

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Explain the latest account snapshot and explicitly surface missing PnL."""
        return "生成收盘复盘：解释目标/当前权重、实际回撤、风险变化与退出意图；若data_gaps声明无盈亏证据，禁止生成当日收益、超额或归因数字。"
