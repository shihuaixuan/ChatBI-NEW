from typing import Any

from pydantic import BaseModel, Field

from apps.chatbi_workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.errors import NodeError
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
    NodeResultStatus,
)


class SqlGenerateInput(BaseModel):
    """SQL 生成节点输入。"""

    question: str
    schema_summary: dict[str, Any]


class SqlGenerateOutput(BaseModel):
    """SQL 生成节点输出。"""

    sql: str


class SqlValidateInput(BaseModel):
    """SQL 校验节点输入。"""

    sql: str


class SqlValidateOutput(BaseModel):
    """SQL 校验节点输出。"""

    valid: bool
    reason: str = "ok"


class PermissionApplyInput(BaseModel):
    """权限应用节点输入。"""

    sql: str
    datasource_id: int


class PermissionApplyOutput(BaseModel):
    """权限应用节点输出。"""

    allowed: bool
    reason: str = "ok"


class SqlExecuteInput(BaseModel):
    """SQL 执行节点输入。"""

    sql: str
    permission: dict[str, Any] = Field(default_factory=dict)


class SqlExecuteOutput(BaseModel):
    """SQL 执行节点输出。"""

    rows: list[dict[str, Any]] = Field(default_factory=list)


class SqlGenerateNode:
    """生成 SQL，但不执行 SQL。"""

    capability = "sql.generate"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            context_request = request.context_view.get("request", {})
            variables = request.context_view.get("variables", {})
            payload = SqlGenerateInput(
                question=context_request["question"],
                schema_summary=variables.get("schema", {}),
            )
            result = self._gateway.invoke(self.capability, payload.model_dump(mode="json"), request.idempotency_key)
            output = SqlGenerateOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.sql": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="SQL_GENERATE_FAILED", message=str(exc), retryable=False),
            )


class SqlValidateNode:
    """校验 SQL，是执行节点之前的强制安全节点。"""

    capability = "sql.validate"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            sql = request.context_view.get("variables", {}).get("sql", {}).get("sql", "")
            payload = SqlValidateInput(sql=sql)
            result = self._gateway.invoke(self.capability, payload.model_dump(mode="json"), request.idempotency_key)
            output = SqlValidateOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.sql_validation": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="SQL_VALIDATE_FAILED", message=str(exc), retryable=False),
            )


class PermissionApplyNode:
    """应用权限策略，是执行节点之前的强制安全节点。"""

    capability = "permission.apply"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            context_request = request.context_view.get("request", {})
            sql = request.context_view.get("variables", {}).get("sql", {}).get("sql", "")
            payload = PermissionApplyInput(sql=sql, datasource_id=context_request["datasource_id"])
            result = self._gateway.invoke(self.capability, payload.model_dump(mode="json"), request.idempotency_key)
            output = PermissionApplyOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.permission": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="PERMISSION_APPLY_FAILED", message=str(exc), retryable=False),
            )


class SqlExecuteNode:
    """执行已校验且已应用权限的 SQL。"""

    capability = "sql.execute"

    def __init__(self, gateway: ChatBICapabilityGateway) -> None:
        self._gateway = gateway

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            variables = request.context_view.get("variables", {})
            payload = SqlExecuteInput(
                sql=variables.get("sql", {}).get("sql", ""),
                permission=variables.get("permission", {}),
            )
            result = self._gateway.invoke(self.capability, payload.model_dump(mode="json"), request.idempotency_key)
            output = SqlExecuteOutput.model_validate(result)
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values={"variables.sql_result": output.model_dump(mode="json")}),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(code="SQL_EXECUTE_FAILED", message=str(exc), retryable=False),
            )
