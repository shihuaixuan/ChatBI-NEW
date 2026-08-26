"""Reasoning Profile：集中定义推理模式的行为差异（doc38 §9.3.1）。

历史上 ``normal`` / ``soft`` 是散落在 Reasoner / ToolExecutor / 可见性函数里的
字符串约定。本模块把它们收敛为显式配置对象，并新增 Research Profile：

- ``fixed_tool_allowlist``：非空时模型只能看到这组工具（Research 只暴露规划
  和完成工具），工具可见性不再走 ChatBI 阶段表；
- ``working_state_builder``：非空时用它的投影代替通用 ``project_working_state``
  （Research 使用独立的受控推理上下文）；
- ``direct_answer_finishes``：纯文本回答是否视为完成。ChatBI 为 True，
  Research 必须为 False——完成只能通过调用 ``finish_research`` 声明；
- ``soft_reminder`` / ``working_state_note``：注入给模型的提示文案。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from apps.chatbi.orchestration.agent.tool_visibility import (
    visible_tool_names as chatbi_visible_tool_names,
)
from apps.chatbi.orchestration.agent.working_state import (
    project_working_state as project_chatbi_working_state,
)

if TYPE_CHECKING:
    from apps.chatbi.orchestration.agent.state import AgentRuntimeState


DEFAULT_WORKING_STATE_NOTE = (
    "该状态由服务端根据可信工具结果生成。请优先选择 recommended 动作；"
    "只有新动作能够补充缺失信息或修正上一错误时，才进行额外探索。"
)

RESEARCH_WORKING_STATE_NOTE = (
    "该状态由服务端根据可信工具结果生成。基于它决定下一个工具调用；"
    "Evidence 产生后使用 assess_research 提交内容充分性判断；需要继续时必须携带"
    "明确缺口和计划增量，结束时调用 finish_research，纯文本回答不构成完成。"
)


@dataclass(frozen=True)
class ReasoningProfile:
    """一次推理会话的模式配置；Reasoner 与宿主循环只读该对象。"""

    name: str
    direct_answer_finishes: bool = True
    soft_reminder: str | None = None
    fixed_tool_allowlist: tuple[str, ...] | None = None
    working_state_builder: (
        Callable[[AgentRuntimeState], dict[str, Any]] | None
    ) = None
    working_state_note: str = field(default=DEFAULT_WORKING_STATE_NOTE)

    def visible_tool_names(
        self,
        state: AgentRuntimeState,
        registered: list[str],
    ) -> list[str]:
        """计算本轮模型可见工具。

        固定白名单模式（Research）不做阶段推进收缩；标准模式沿用
        ChatBI 的进度驱动可见性表。
        """

        if self.fixed_tool_allowlist is not None:
            allow = set(self.fixed_tool_allowlist)
            return [name for name in registered if name in allow]
        return chatbi_visible_tool_names(state, self.name, registered)

    def project_working_state(
        self,
        state: AgentRuntimeState,
        available_tools: list[str],
    ) -> dict[str, Any]:
        """构造本轮注入模型的 Working State 载荷。"""

        if self.working_state_builder is not None:
            return self.working_state_builder(state)
        return project_chatbi_working_state(state, self.name, available_tools)


NORMAL_PROFILE = ReasoningProfile(name="normal")

SOFT_PROFILE = ReasoningProfile(
    name="soft",
    soft_reminder=(
        "<system-reminder>预算接近上限。已有 SQL 时立即 execute_sql，"
        "已有执行结果时立即 finish；若关键歧义未消可 clarify；"
        "不要启动新的检索或 SQL 探索。</system-reminder>"
    ),
)

RESEARCH_PROFILE = ReasoningProfile(
    name="research",
    direct_answer_finishes=False,
    # 模型只负责提交计划修订或完成判断；查询、计算和检查由 DAG 驱动执行。
    fixed_tool_allowlist=("assess_research", "finish_research"),
    working_state_note=RESEARCH_WORKING_STATE_NOTE,
)

REASONING_PROFILES: dict[str, ReasoningProfile] = {
    NORMAL_PROFILE.name: NORMAL_PROFILE,
    SOFT_PROFILE.name: SOFT_PROFILE,
    RESEARCH_PROFILE.name: RESEARCH_PROFILE,
}


def get_reasoning_profile(mode: str) -> ReasoningProfile:
    """按名称解析 Profile；未知模式直接拒绝，不静默回退。"""

    profile = REASONING_PROFILES.get(mode)
    if profile is None:
        raise ValueError(f"Unsupported reasoning mode: {mode}")
    return profile


__all__ = [
    "NORMAL_PROFILE",
    "REASONING_PROFILES",
    "RESEARCH_PROFILE",
    "SOFT_PROFILE",
    "ReasoningProfile",
    "get_reasoning_profile",
]
