from pydantic import BaseModel, Field

from sqlbot_platform.workflow_engine.domain.context import WorkflowContext
from sqlbot_platform.workflow_engine.domain.definition import (
    NodeDefinition,
    WorkflowDefinition,
)
from sqlbot_platform.workflow_engine.domain.execution import NodeExecutionResult
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)


class ConditionDecision(BaseModel):
    """条件求值的可审计结果，不保存模型思维链。"""

    matched: bool
    reason_code: str = Field(min_length=1)
    reason_summary: str = Field(min_length=1, max_length=1024)


class RouteDecision(BaseModel):
    """Runtime 推进游标所需的完整路由结论。"""

    source: str
    target: str
    condition: str | None = None
    reason_code: str
    reason_summary: str
    condition_errors: list[str] = Field(default_factory=list)


class RoutingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ConditionRouter:
    """按优先级执行无副作用条件并选择下一条边。"""

    def __init__(self, conditions: ConditionRegistry) -> None:
        self._conditions = conditions

    def select(
        self,
        definition: WorkflowDefinition,
        node: NodeDefinition,
        context: WorkflowContext,
        result: NodeExecutionResult,
    ) -> RouteDecision:
        edges = [edge for edge in definition.edges if edge.source == node.name]
        conditional_edges = sorted(
            (edge for edge in edges if edge.condition is not None),
            key=lambda edge: edge.priority,
        )
        default_edge = next((edge for edge in edges if edge.condition is None), None)
        condition_errors: list[str] = []

        for edge in conditional_edges:
            condition_name = edge.condition
            if condition_name is None:
                continue
            evaluator = self._conditions.get(condition_name)
            try:
                decision = evaluator.evaluate(context, result)
            except Exception:
                # 条件异常不能中断后续边求值，也不能把异常细节暴露给公开路由事件。
                condition_errors.append(condition_name)
                continue
            if decision.matched:
                return RouteDecision(
                    source=node.name,
                    target=edge.target,
                    condition=condition_name,
                    reason_code=decision.reason_code,
                    reason_summary=decision.reason_summary,
                    condition_errors=condition_errors,
                )

        if default_edge is not None:
            return RouteDecision(
                source=node.name,
                target=default_edge.target,
                reason_code="DEFAULT_EDGE",
                reason_summary="没有显式条件命中，使用默认边",
                condition_errors=condition_errors,
            )

        raise RoutingError(
            "ROUTE_NOT_FOUND",
            f"节点 {node.name!r} 没有可用的下一跳",
        )
