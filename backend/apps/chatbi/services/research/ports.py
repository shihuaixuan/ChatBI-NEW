"""Research 子域的稳定端口和工具准备结果。

Research 工具不能直接复用旧 Agent 工具的 ``ToolResult``。旧工具结果同时承担
模型消息和领域观察，无法表达新 ReAct 契约要求的类型化结果负载。本文件只定义
Research Runtime 与具体工具之间的最小协议，具体查询、计算和检索实现留在各自
阶段接入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from apps.chatbi.models.dto.research_agent import ResearchActionType

ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
DomainT = TypeVar("DomainT")


@dataclass(frozen=True, slots=True)
class ResearchToolCostEstimate:
    """一次工具调用在 Runtime 预算中的最小成本估算。"""

    query_calls: int = 0
    compute_calls: int = 0
    semantic_search_calls: int = 0
    wall_time_ms: int = 0
    query_cost: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.query_calls,
            self.compute_calls,
            self.semantic_search_calls,
            self.wall_time_ms,
        )
        if any(value < 0 for value in values) or self.query_cost < 0:
            raise ValueError("RESEARCH_TOOL_COST_ESTIMATE_INVALID")


@dataclass(frozen=True, slots=True)
class PreparedResearchAction(Generic[ArgsT, DomainT]):
    """工具 prepare 阶段产出的可信执行输入。"""

    args: ArgsT
    action_fingerprint: str
    cost: ResearchToolCostEstimate
    domain: DomainT | None = None

    def __post_init__(self) -> None:
        if not self.action_fingerprint.strip():
            raise ValueError("RESEARCH_ACTION_FINGERPRINT_REQUIRED")


class ResearchTool(Protocol, Generic[ArgsT, ResultT, DomainT]):
    """Research 工具的统一协议。

    ``prepare`` 负责参数关系、权限边界和领域对象准备；``execute`` 只返回结果
    负载，ToolResult 的状态、错误和调用标识统一由 Runtime 构造。
    """

    name: ResearchActionType | str
    args_model: type[ArgsT]
    result_model: type[ResultT]
    parallel_safe: bool

    def prepare(
        self,
        context: Any,
        args: ArgsT,
    ) -> PreparedResearchAction[ArgsT, DomainT]: ...

    def execute(
        self,
        context: Any,
        prepared: PreparedResearchAction[ArgsT, DomainT],
    ) -> ResultT: ...


class ResearchPolicyPromptBuilder(Protocol):
    """构造 Research Policy 单轮决策提示词。"""

    def build(self, context: dict[str, Any]) -> tuple[str, str]: ...


__all__ = [
    "PreparedResearchAction",
    "ResearchPolicyPromptBuilder",
    "ResearchTool",
    "ResearchToolCostEstimate",
]
