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


class SchemaRetrieveInput(BaseModel):
    """Schema 检索节点输入。"""

    question: str
    datasource_id: int
    understanding: dict[str, Any] = Field(default_factory=dict)


class SchemaRetrieveOutput(BaseModel):
    """Schema 检索节点输出。"""

    tables: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)


class SchemaRetrieveNode:
    """调用 Schema 检索能力，并把轻量证据摘要写入 variables.schema。"""

    capability = "schema.retrieve"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            context_request = request.context_view.get("request", {})
            variables = request.context_view.get("variables", {})
            payload = SchemaRetrieveInput(
                question=context_request["question"],
                datasource_id=context_request["datasource_id"],
                understanding=variables.get("understanding", {}),
            )
            result = self._gateway.invoke(
                self.capability,
                payload.model_dump(mode="json"),
                request.idempotency_key,
            )
            output = SchemaRetrieveOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.schema": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="SCHEMA_RETRIEVE_FAILED", message=str(exc), retryable=False),
            )
