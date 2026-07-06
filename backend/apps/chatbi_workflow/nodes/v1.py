
from apps.chatbi_workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.workflow_engine.domain.context import ContextPatch
from apps.workflow_engine.domain.errors import NodeError
from apps.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
    NodeResultStatus,
)


class ChatBIV1CapabilityNode:
    """ChatBI v1 通用能力节点，负责调用网关并把结果写入指定上下文路径。

    业务能力抛出的异常不再终止整个 Run：异常被转换为 `variables.node_failure`
    的结构化失败记录并成功返回，由图定义中的 `node.degraded` 条件边把流程
    路由到解释性回答节点。只有引擎层错误才应该导致 Run 失败。
    """

    def __init__(
        self,
        gateway: ChatBICapabilityGateway,
        capability: str,
        output_path: str,
        output_model: type | None = None,
        mirror_output_paths: tuple[str, ...] = (),
    ) -> None:
        self._gateway = gateway
        self._capability = capability
        self._output_path = output_path
        self._output_model = output_model
        self._mirror_output_paths = mirror_output_paths

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            result = self._gateway.invoke(
                self._capability,
                {
                    "run_id": request.run_id,
                    "request": request.context_view.get("request", {}),
                    "conversation": request.context_view.get("conversation", {}),
                    "variables": request.context_view.get("variables", {}),
                    "inputs": request.inputs,
                    "node_name": request.node_name,
                },
                request.idempotency_key,
            )
            if self._output_model is not None:
                result = self._output_model.model_validate(result).model_dump(mode="json")
            set_values = {self._output_path: result}
            set_values.update(
                {path: result for path in self._mirror_output_paths}
            )
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(set_values=set_values),
            )
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.SUCCEEDED,
                patch=ContextPatch(
                    set_values={
                        "variables.node_failure": {
                            "node": request.node_name,
                            "capability": self._capability,
                            "error_code": f"{self._capability.upper().replace('.', '_')}_FAILED",
                            "message": str(exc),
                        }
                    }
                ),
            )


class ChatBIV1InteractionNode:
    """ChatBI v1 人机交互节点，创建澄清请求并暂停 Run。"""

    def __init__(self, gateway: ChatBICapabilityGateway, capability: str, response_path: str) -> None:
        self._gateway = gateway
        self._capability = capability
        self._response_path = response_path

    def execute(self, request: NodeExecutionRequest) -> NodeExecutionResult:
        try:
            spec = self._gateway.invoke(
                self._capability,
                {
                    "run_id": request.run_id,
                    "request": request.context_view.get("request", {}),
                    "conversation": request.context_view.get("conversation", {}),
                    "variables": request.context_view.get("variables", {}),
                    "inputs": request.inputs,
                    "node_name": request.node_name,
                },
                request.idempotency_key,
            )
            interaction = {
                "prompt": spec.get("prompt"),
                "options": spec.get("options", []),
                "response_schema": spec.get("response_schema", {"type": "object"}),
                "allowed_update_paths": spec.get("allowed_update_paths", [self._response_path]),
            }
            return NodeExecutionResult(status=NodeResultStatus.WAITING_INPUT, interaction=interaction)
        except Exception as exc:
            return NodeExecutionResult(
                status=NodeResultStatus.FAILED,
                error=NodeError(
                    code=f"{self._capability.upper().replace('.', '_')}_FAILED",
                    message=str(exc),
                    retryable=False,
                ),
            )
