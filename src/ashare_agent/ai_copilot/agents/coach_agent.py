from ...core.contracts import CopilotAgentType, CopilotReportType
from .base import BaseCopilotAgent


class CoachAgent(BaseCopilotAgent):
    """Extract evidence-backed discipline lessons as unconfirmed memory candidates."""

    agent_type = CopilotAgentType.COACH
    supported_reports = frozenset({CopilotReportType.COACH_REVIEW})

    def task_instructions(self, report_type: CopilotReportType) -> str:
        """Ask for observable behavior patterns rather than personality guesses."""
        return "根据已保存目标/当前权重、风险与退出证据总结纪律；只把可引用事实写成待用户确认的memory_candidates。"
