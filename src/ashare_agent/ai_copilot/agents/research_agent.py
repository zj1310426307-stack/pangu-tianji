from ...core.contracts import CopilotAgentType, CopilotReportType
from .base import BaseCopilotAgent


class ResearchAgent(BaseCopilotAgent):
    """Explain deterministic selection and factor evidence without predictions."""

    agent_type = CopilotAgentType.RESEARCH
    supported_reports = frozenset(
        {CopilotReportType.MORNING_REPORT, CopilotReportType.STOCK_ANALYSIS}
    )

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Select morning-overview or single-stock research framing."""
        if report_type is CopilotReportType.MORNING_REPORT:
            return "生成研究晨报：解释Top排名、主要因子、数据时点和风险标签，不做盘中预测。"
        return "解释指定股票在该run_id中的排名、因子、风险与失效边界。"
