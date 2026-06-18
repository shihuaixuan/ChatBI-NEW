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


class TrackingGateway(PlaceholderChatBICapabilityGateway):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        return super().invoke(capability, request, idempotency_key)


def _runtime(gateway: TrackingGateway) -> GraphRuntime:
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, gateway)
    register_chatbi_conditions(conditions)
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    registry.publish(build_chatbi_v1_definition())
    store = InMemoryRunStore()
    events = InMemoryEventPublisher()
    return GraphRuntime(
        registry=registry,
        run_store=store,
        scheduler=NodeScheduler(handlers),
        router=ConditionRouter(conditions),
        context_patcher=ContextPatcher(),
        checkpoint_manager=CheckpointManager(store, events),
        lease=InMemoryRunLease(),
        interaction_manager=InteractionManager(),
    )


def _execute(question: str) -> tuple[TrackingGateway, object]:
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        f"chatbi-v1-{abs(hash(question))}",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": question, "datasource_id": 1, "tenant_id": 10, "user_id": 20}),
    )
    return gateway, runtime.execute(run.run_id)


def test_chatbi_v1_placeholder_main_path_executes_full_graph_to_final_reply():
    gateway, outcome = _execute("最近 7 天销售额")

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    assert gateway.calls == [
        "question.classify",
        "question.rewrite",
        "question.draw_image_profile",
        "intent.recognize",
        "knowledge.retrieve",
        "sql.generate",
        "sql.execute",
        "answer.generate",
        "question.recommend",
        "answer.compose",
    ]
    assert outcome.context.variables["final_reply"]["final_answer"] == "这是图工作流占位回答：最近 7 天销售额"
    assert outcome.context.variables["completed"] is True


def test_chatbi_v1_placeholder_forbidden_question_stops_before_sql():
    gateway, outcome = _execute("越权 查看其他部门销售额")

    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls == ["question.classify", "answer.reject", "answer.compose"]
    assert "sql.generate" not in gateway.calls
    assert outcome.context.variables["final_reply"]["final_answer"] == "当前问题无法在权限范围内回答。"


def test_chatbi_v1_placeholder_chitchat_stops_before_knowledge_retrieval():
    gateway, outcome = _execute("你好，今天天气怎么样")

    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls == ["question.classify", "answer.chitchat", "answer.compose"]
    assert "knowledge.retrieve" not in gateway.calls
    assert outcome.context.variables["final_reply"]["final_answer"] == "你好，我可以帮你分析业务数据问题。"


def test_chatbi_v1_placeholder_knowledge_miss_skips_sql():
    gateway, outcome = _execute("知识未命中 的业务问题")

    assert outcome.status is RunStatus.SUCCEEDED
    assert "knowledge.retrieve" in gateway.calls
    assert "sql.generate" not in gateway.calls
    assert gateway.calls[-2:] == ["question.recommend", "answer.compose"]


def test_chatbi_v1_placeholder_sql_failure_routes_to_error_handler():
    gateway, outcome = _execute("最近 7 天销售额 SQL失败")

    assert outcome.status is RunStatus.SUCCEEDED
    assert "sql.handle_error" in gateway.calls
    assert gateway.calls.index("sql.handle_error") > gateway.calls.index("sql.execute")
    assert outcome.context.variables["sql_error"]["error_code"] == "PLACEHOLDER_SQL_FAILED"


def test_chatbi_v1_placeholder_rewrite_clarification_can_resume_to_success():
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-rewrite-clarification",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "需要澄清的问题", "datasource_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    pending_interaction_id = paused.context.control.pending_interaction_id
    resumed = runtime.resume(
        run.run_id,
        pending_interaction_id or "",
        {"metric": "sales_amount"},
        tenant_id=10,
        user_id=20,
    )

    assert paused.status is RunStatus.WAITING_INPUT
    assert paused.current_node == "ask_rewrite_clarification"
    assert pending_interaction_id is not None
    assert resumed.status is RunStatus.SUCCEEDED
    assert gateway.calls.count("question.rewrite") == 2
    assert gateway.calls[-2:] == ["question.recommend", "answer.compose"]


def test_chatbi_v1_placeholder_intent_clarification_can_resume_to_success():
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-intent-clarification",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "意图不明 的销售问题", "datasource_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    pending_interaction_id = paused.context.control.pending_interaction_id
    resumed = runtime.resume(
        run.run_id,
        pending_interaction_id or "",
        {"intent": "metric_query"},
        tenant_id=10,
        user_id=20,
    )

    assert paused.status is RunStatus.WAITING_INPUT
    assert paused.current_node == "ask_intent_clarification"
    assert resumed.status is RunStatus.SUCCEEDED
    assert gateway.calls.count("intent.recognize") == 2
    assert "sql.execute" in gateway.calls


def test_chatbi_v1_placeholder_metric_selection_can_resume_to_success():
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-metric-selection",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "多指标 销售分析", "datasource_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    pending_interaction_id = paused.context.control.pending_interaction_id
    resumed = runtime.resume(
        run.run_id,
        pending_interaction_id or "",
        {"metric": "sales_amount"},
        tenant_id=10,
        user_id=20,
    )

    assert paused.status is RunStatus.WAITING_INPUT
    assert paused.current_node == "ask_metric_selection"
    assert resumed.status is RunStatus.SUCCEEDED
    assert gateway.calls.count("knowledge.retrieve") == 1
    assert gateway.calls.index("sql.generate") > gateway.calls.index("interaction.ask_metric_selection")
    assert "sql.execute" in gateway.calls
