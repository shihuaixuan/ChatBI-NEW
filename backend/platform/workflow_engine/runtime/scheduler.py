from sqlbot_platform.workflow_engine.domain.context import WorkflowContext
from sqlbot_platform.workflow_engine.domain.definition import NodeDefinition
from sqlbot_platform.workflow_engine.domain.execution import (
    NodeExecutionRequest,
    NodeExecutionResult,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.runtime.mapping import (
    MappingError,
    MappingResolver,
)


class SchedulerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class NodeScheduler:
    """把图节点定义适配成一次受控 Handler 调用。"""

    def __init__(
        self,
        handlers: HandlerRegistry,
        mapping_resolver: MappingResolver | None = None,
    ) -> None:
        self._handlers = handlers
        self._mapping = mapping_resolver or MappingResolver()
        # P0 使用内存幂等结果表。持久化阶段会由 NodeExecution Repository 接管。
        self._completed_results: dict[str, NodeExecutionResult] = {}

    def execute(
        self,
        run_id: str,
        node: NodeDefinition,
        attempt: int,
        context: WorkflowContext,
    ) -> NodeExecutionResult:
        # 同一个节点可能因澄清恢复或显式循环被再次访问，幂等键必须区分访问轮次。
        visit_count = context.control.loop_iterations.get(node.name, 0)
        idempotency_key = f"{run_id}:{node.name}:{visit_count}:{attempt}"
        cached = self._completed_results.get(idempotency_key)
        if cached is not None:
            return cached.model_copy(deep=True)

        try:
            handler = self._handlers.get(node.handler)
        except KeyError as exc:
            raise SchedulerError("HANDLER_NOT_FOUND", str(exc)) from exc

        try:
            inputs = self._mapping.resolve_inputs(context, node.input_mapping)
        except MappingError as exc:
            raise SchedulerError("NODE_INPUT_MAPPING_FAILED", str(exc)) from exc

        request = NodeExecutionRequest(
            run_id=run_id,
            node_name=node.name,
            attempt=attempt,
            idempotency_key=idempotency_key,
            inputs=inputs,
            context_view=context.model_dump(mode="python"),
        )
        result = handler.execute(request)
        if not isinstance(result, NodeExecutionResult):
            raise SchedulerError(
                "INVALID_NODE_RESULT",
                f"处理器 {node.handler!r} 未返回 NodeExecutionResult",
            )
        self._completed_results[idempotency_key] = result.model_copy(deep=True)
        return result.model_copy(deep=True)
