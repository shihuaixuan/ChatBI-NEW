from pydantic import BaseModel

from apps.workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.errors import NodeError
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
    NodeResultStatus,
)


class QueryUnderstandInput(BaseModel):
    """问题理解节点输入。"""

    question: str
    datasource_id: int | None = None


class QueryUnderstandOutput(BaseModel):
    """问题理解节点输出。"""

    normalized_question: str


class QueryUnderstandNode:
    """调用问题理解能力，并把结构化结果写入 variables.understanding。"""

    capability = "query.understand"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            context_request = request.context_view.get("request", {})
            payload = QueryUnderstandInput(
                question=context_request["question"],
                datasource_id=context_request.get("datasource_id"),
            )
            result = self._gateway.invoke(
                self.capability,
                payload.model_dump(mode="json"),
                request.idempotency_key,
            )
            output = QueryUnderstandOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.understanding": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="QUERY_UNDERSTAND_FAILED", message=str(exc), retryable=False),
            )
