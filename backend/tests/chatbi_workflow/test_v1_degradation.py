"""Step 0 缺陷回归测试：节点异常降级为解释性回答（A3）。

修复前：能力 adapter 抛出的任何异常都会把整个 Run 置为 FAILED，用户只收到
run.failed 事件，图中的 generate_question_answer 兜底节点永远走不到。
修复后：异常转换为 variables.node_failure 结构化记录，node.degraded 条件边
把流程路由到解释性回答，Run 正常完成。
"""

from apps.chatbi_workflow import runtime as chatbi_runtime
from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi_workflow.conditions.core import register_chatbi_conditions
from apps.chatbi_workflow.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.run import RunStatus
from apps.workflow_engine.infrastructure.memory import (
    InMemoryEventPublisher,
    InMemoryRunStore,
)
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.registry.definition_validator import DefinitionValidator
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry
from apps.workflow_engine.runtime.checkpoint_manager import CheckpointManager
from apps.workflow_engine.runtime.context_patcher import ContextPatcher
from apps.workflow_engine.runtime.graph_runtime import GraphRuntime
from apps.workflow_engine.runtime.interaction import InteractionManager
from apps.workflow_engine.runtime.lease import InMemoryRunLease
from apps.workflow_engine.runtime.router import ConditionRouter
from apps.workflow_engine.runtime.scheduler import NodeScheduler


class FailingCapabilityGateway(PlaceholderChatBICapabilityGateway):
    def __init__(self, failing_capability: str, error: str) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._failing_capability = failing_capability
        self._error = error

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == self._failing_capability:
            raise RuntimeError(self._error)
        return super().invoke(capability, request, idempotency_key)


def _run(gateway: FailingCapabilityGateway, question: str = "最近 7 天销售额"):
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())
    store = InMemoryRunStore()
    runtime = GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, InMemoryEventPublisher()),
        lease=InMemoryRunLease(),
        interaction_manager=InteractionManager(),
        interaction_response_patcher=chatbi_runtime.ChatBIV1InteractionResponsePatcher(),
    )
    runtime.create_run(
        "chatbi-v1-degrade",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": question, "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )
    return runtime.execute("chatbi-v1-degrade")


def test_classification_failure_degrades_to_explanatory_answer():
    gateway = FailingCapabilityGateway("question.classify", "CLASSIFICATION_MODEL_CALL_FAILED")

    outcome = _run(gateway)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    failure = outcome.context.variables["node_failure"]
    assert failure["node"] == "classify_question"
    assert failure["error_code"] == "QUESTION_CLASSIFY_FAILED"
    # 失败被解释而不是吞掉：回答节点执行且最终回复存在。
    assert "answer.generate" in gateway.calls
    assert outcome.context.variables["final_reply"]["final_answer"]


def test_sql_generation_failure_degrades_without_executing_sql():
    gateway = FailingCapabilityGateway("sql.generate", "SQL_VALIDATE_FAILED")

    outcome = _run(gateway)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    assert outcome.context.variables["node_failure"]["error_code"] == "SQL_GENERATE_FAILED"
    assert "sql.execute" not in gateway.calls
    assert "answer.generate" in gateway.calls


def test_degraded_answer_fallback_mentions_error_code():
    """回答模型本身也不可用时，兜底文案必须携带失败原因而不是通用话术。"""

    from apps.chatbi_workflow.capabilities.adapters.answer import AnswerAdapter

    def broken_model(_prompt):
        raise RuntimeError("LLM_UNAVAILABLE")

    adapter = AnswerAdapter(model_client=broken_model)
    result = adapter.generate(
        {
            "request": {"question": "最近 7 天销售额"},
            "variables": {
                "node_failure": {
                    "node": "generate_sql",
                    "capability": "sql.generate",
                    "error_code": "SQL_GENERATE_FAILED",
                    "message": "SQL_VALIDATE_FAILED",
                }
            },
        }
    )

    assert "SQL_GENERATE_FAILED" in result["answer"]
    assert "answer_generation_degraded" in result["warnings"]
