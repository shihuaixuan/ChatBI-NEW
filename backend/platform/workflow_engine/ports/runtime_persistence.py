from typing import Any, Protocol

from sqlbot_platform.workflow_engine.domain.definition import NodeDefinition
from sqlbot_platform.workflow_engine.domain.execution import NodeExecutionResult
from sqlbot_platform.workflow_engine.domain.interaction import InteractionRequest
from sqlbot_platform.workflow_engine.domain.run import WorkflowRun


class RouteDecisionView(Protocol):
    """节点执行记录所需的路由结果字段。"""

    target: str
    condition: str | None
    reason_code: str
    reason_summary: str


class InteractionStore(Protocol):
    """运行时创建、读取和回答交互请求的持久化端口。"""

    def create(
        self,
        run_id: str,
        node_name: str,
        spec: dict[str, Any],
    ) -> InteractionRequest: ...

    def get(self, interaction_id: str | None) -> InteractionRequest: ...

    def answer(
        self,
        interaction_id: str,
        response: dict[str, Any],
    ) -> InteractionRequest: ...


class NodeExecutionRecorder(Protocol):
    """节点执行尝试记录端口。"""

    def record(
        self,
        run: WorkflowRun,
        node: NodeDefinition,
        result: NodeExecutionResult,
        route: RouteDecisionView | None = None,
    ) -> None: ...


__all__ = ["InteractionStore", "NodeExecutionRecorder", "RouteDecisionView"]
