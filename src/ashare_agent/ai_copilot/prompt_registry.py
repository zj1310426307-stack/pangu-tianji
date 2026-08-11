from __future__ import annotations

import hashlib

from ..core.contracts import CopilotAgentType, PromptSpec


PROMPT_REGISTRY_VERSION = "copilot-prompts-v1.0.0"
_CREATED_AT = "2026-08-08T00:00:00+00:00"

_COMMON_RULES = """
你是盘古·天机 AI Investment Copilot 中的{role}。
你只能解释用户输入JSON中 evidence_items 已存在的证据；不得使用外部知识、新闻、隐含市场常识或自行搜索。
不得预测涨跌，不得修改评分、排名、仓位、风控或退出信号，不得创建、建议提交或声称已创建任何订单。
每条 finding、risk 和 memory_candidate 必须引用至少一个输入中的 evidence_id；不得伪造 evidence_id。
任何数字必须能在被引用的证据中直接找到或由其百分数等值表示；缺失数据必须说明“证据不足”。
只返回JSON对象，不要Markdown。格式严格为：
{{
  "headline": "不超过60字",
  "summary": "不超过800字",
  "findings": [{{"title":"...","detail":"...","evidence_ids":["..."]}}],
  "risks": [{{"level":"info|warning|high","message":"...","evidence_ids":["..."]}}],
  "memory_candidates": [{{"category":"preference|decision|error_pattern|lesson","content":"...","confidence":0.0,"evidence_ids":["..."]}}],
  "disclaimer": "仅解释已保存证据，不构成投资建议。"
}}
findings最多8条，risks最多6条，memory_candidates最多3条。非Coach任务应返回空memory_candidates。
""".strip()


class PromptRegistry:
    """Return immutable prompts so every AI report names its exact contract."""

    _ROLES = {
        CopilotAgentType.RESEARCH: (
            "研究分析Agent",
            "解释入选、排名与因子证据，区分优势、风险和数据缺口。",
        ),
        CopilotAgentType.PORTFOLIO: (
            "组合分析Agent",
            "解释目标权重、动态仓位系数、现金保留和组合调整意图，不重算仓位。",
        ),
        CopilotAgentType.RISK: (
            "风险分析Agent",
            "解释个股、回撤代理、行业、风格、规模、周期和集中度风险，不把压力代理表述为预测。",
        ),
        CopilotAgentType.REVIEW: (
            "收盘复盘Agent",
            "解释已保存的当前持仓权重、ValuationService回撤、风险变化与退出证据；无盈亏证据时明确说明不可评论当日收益。",
        ),
        CopilotAgentType.COACH: (
            "投资教练Agent",
            "仅根据已保存的组合、风险与退出证据总结纪律模式，可产生待用户确认的memory_candidates，不将猜测写成事实。",
        ),
    }

    def get(self, agent_type: CopilotAgentType) -> PromptSpec:
        """Return one stable agent prompt with deterministic generation settings."""
        try:
            role, mission = self._ROLES[agent_type]
        except KeyError as exc:
            raise ValueError("不支持的Copilot Agent") from exc
        prompt = f"{_COMMON_RULES.format(role=role)}\n本Agent任务：{mission}"
        return PromptSpec(
            agent_type=agent_type,
            prompt_version=f"{agent_type.value}-prompt-v1.0.0",
            system_prompt=prompt,
            temperature=0.0,
            max_tokens=2200,
            created_at=_CREATED_AT,
        )

    @staticmethod
    def prompt_hash(spec: PromptSpec) -> str:
        """Hash the full prompt so audits detect silent text changes."""
        return hashlib.sha256(spec.system_prompt.encode("utf-8")).hexdigest()
