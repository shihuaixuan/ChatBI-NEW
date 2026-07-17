from typing import Any

from pydantic import BaseModel, Field

from apps.workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.errors import NodeError
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
    NodeResultStatus,
)


class AnswerGenerateInput(BaseModel):
    """答案生成节点输入。"""

    question: str
    variables: dict[str, Any] = Field(default_factory=dict)


class AnswerGenerateOutput(BaseModel):
    """答案生成节点输出。"""

    answer: str
    warnings: list[str] = Field(default_factory=list)


class AnswerGenerateNode:
    """根据 SQL 结果或安全拒绝原因生成最终回答。"""

    capability = "answer.generate"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            context_request = request.context_view.get("request", {})
            variables = request.context_view.get("variables", {})
            payload = AnswerGenerateInput(question=context_request["question"], variables=variables)
            result = self._gateway.invoke(self.capability, payload.model_dump(mode="json"), request.idempotency_key)
            output = AnswerGenerateOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.answer": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="ANSWER_GENERATE_FAILED", message=str(exc), retryable=False),
            )


class FinishNode:
    """终止节点，只做完成标记，确保 Runtime 实际执行 terminal 节点。"""

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        return NodeExecutionResult(
            status=NodeResultStatus.SUCCEEDED,
            patch=ContextPatch(set_values={"variables.completed": True}),
        )
