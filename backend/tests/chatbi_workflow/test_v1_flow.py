from apps.chatbi_workflow import runtime as chatbi_runtime
from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    HeadlessKnowledgeAdapter,
)
from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi_workflow.conditions.core import register_chatbi_conditions
from apps.chatbi_workflow.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.headless.schemas import DataSetSchema, SchemaElement
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


class FakeHeadlessSchemaBuilder:
    def __init__(self, schema: DataSetSchema) -> None:
        self.schema = schema
        self.calls: list[tuple[int, int]] = []

    def build_dataset_schema(self, oid: int, dataset_id: int) -> DataSetSchema:
        self.calls.append((oid, dataset_id))
        return self.schema


class RealKnowledgeGateway(TrackingGateway):
    def __init__(self, knowledge_adapter: HeadlessKnowledgeAdapter) -> None:
        super().__init__()
        self._knowledge_adapter = knowledge_adapter

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "knowledge.retrieve":
            return self._knowledge_adapter.retrieve(request)
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class RepairableSqlGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "sql.generate":
            return {"sql": "select * from missing_table", "datasource_id": 5}
        if capability == "sql.execute" and self.calls.count("sql.execute") == 1:
            return {
                "status": "failed",
                "rows": [],
                "row_count": 0,
                "fields": [],
                "execution_ms": 0,
                "error_code": "sql_execute_error",
                "message": "Table 'missing_table' doesn't exist",
            }
        if capability == "sql.execute":
            return {
                "status": "succeeded",
                "rows": [{"visit_uv": 123}],
                "row_count": 1,
                "fields": ["visit_uv"],
                "execution_ms": 1,
            }
        if capability == "sql.handle_error":
            return {
                "error_code": "sql_execute_error",
                "message": "SQL 执行失败：Table 'missing_table' doesn't exist",
                "retryable": True,
                "repair_hint": "表不存在，请基于已命中的语义表重新生成 SQL。",
                "repair_plan": {
                    "action": "regenerate_sql",
                    "reason": "SQL 引用了不存在的表",
                    "retryable": True,
                    "candidate_tables": ["stall_traffic_1d"],
                },
            }
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


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
        interaction_response_patcher=chatbi_runtime.ChatBIV1InteractionResponsePatcher(),
    )


def _execute(question: str) -> tuple[TrackingGateway, object]:
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        f"chatbi-v1-{abs(hash(question))}",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": question, "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
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
    assert gateway.calls.count("sql.generate") == 1
    assert outcome.context.variables["sql_error"]["error_code"] == "PLACEHOLDER_SQL_FAILED"


def test_chatbi_v1_retryable_sql_error_regenerates_sql_before_answer():
    gateway = RepairableSqlGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-repairable-sql-error",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今日访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls.count("sql.generate") == 2
    assert gateway.calls.count("sql.execute") == 2
    assert gateway.calls.index("sql.generate") < gateway.calls.index("sql.execute")
    assert gateway.calls.index("sql.handle_error") < gateway.calls.index("answer.generate")
    assert outcome.context.variables["sql_error"]["repair_plan"]["action"] == "regenerate_sql"
    assert outcome.context.variables["sql_execution"]["status"] == "succeeded"


def test_chatbi_v1_placeholder_rewrite_clarification_can_resume_to_success():
    gateway = TrackingGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-rewrite-clarification",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "需要澄清的问题", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
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
    interaction = runtime._interactions.get(pending_interaction_id)
    assert interaction.prompt == "请补充要分析的指标。"
    assert interaction.options[:3] == [
        {"label": "访问人数", "value": {"metric": "访问人数"}},
        {"label": "销售额", "value": {"metric": "销售额"}},
        {"label": "订单数", "value": {"metric": "订单数"}},
    ]
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
        WorkflowContext(request={"question": "意图不明 的销售问题", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
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
    interaction = runtime._interactions.get(pending_interaction_id)
    assert interaction.prompt == "当前问题的指标不够明确，请确认你想分析的指标或分析方式。"
    assert interaction.options[0] == {"label": "查指标数值", "value": {"intent": "metric_query"}}
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
        WorkflowContext(request={"question": "多指标 销售分析", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
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
    interaction = runtime._interactions.get(pending_interaction_id)
    assert interaction.options == [
        {"label": "sales_amount", "value": "sales_amount"},
        {"label": "gross_profit", "value": "gross_profit"},
    ]
    assert resumed.status is RunStatus.SUCCEEDED
    assert gateway.calls.count("knowledge.retrieve") == 1
    assert gateway.calls.index("sql.generate") > gateway.calls.index("interaction.ask_metric_selection")
    assert "sql.execute" in gateway.calls
    knowledge = resumed.context.variables["knowledge"]
    assert knowledge["status"] == "hit"
    assert knowledge["metrics"] == ["sales_amount"]
    assert knowledge["ambiguities"] == []
    assert knowledge["selected_assets"]["metrics"] == [
        {
            "asset_id": "sales_amount",
            "biz_name": "sales_amount",
            "display_name": "sales_amount",
            "source": "user_selected",
        }
    ]
    assert knowledge["slot_bindings"]["metrics"] == [
        {
            "asset_id": "sales_amount",
            "biz_name": "sales_amount",
            "display_name": "sales_amount",
            "confidence": 1.0,
            "source": "user_selected",
        }
    ]
    assert knowledge["decision"] == {
        "status": "user_selected",
        "strategy": "metric_selection",
        "reason": "用户已确认指标",
    }


def test_chatbi_v1_graph_passes_user_question_to_real_headless_knowledge_node():
    traffic_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=100,
        name="访问量",
        biz_name="visit_uv",
        type="METRIC",
        description="店铺流量 访问人数 核心指标",
        default_agg="SUM",
        fields=["visit_uv"],
    )
    trade_metric = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=10,
        id=101,
        name="成交订单数",
        biz_name="order_cnt",
        type="METRIC",
        description="店铺交易 订单成交 指标",
        default_agg="SUM",
        fields=["order_cnt"],
    )
    schema = DataSetSchema(
        data_set=SchemaElement(
            data_set_id=20,
            data_set_name="经营分析",
            id=20,
            name="经营分析",
            biz_name="business_bi",
            type="DATASET",
        ),
        models=[{"id": 10, "name": "经营模型", "biz_name": "business_model", "tableQuery": "business_daily"}],
        metrics=[traffic_metric, trade_metric],
        dimensions=[],
    )
    schema_builder = FakeHeadlessSchemaBuilder(schema)
    gateway = RealKnowledgeGateway(HeadlessKnowledgeAdapter(schema_builder=schema_builder))
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-real-knowledge-from-user-question",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今日店铺流量", "dataset_id": 20, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)
    knowledge = outcome.context.variables["knowledge"]

    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls[:5] == [
        "question.classify",
        "question.rewrite",
        "question.draw_image_profile",
        "intent.recognize",
        "knowledge.retrieve",
    ]
    assert schema_builder.calls == [(10, 20)]
    assert knowledge["status"] == "hit"
    assert knowledge["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert knowledge["selected_assets"]["metrics"][0]["biz_name"] == "visit_uv"
    assert knowledge["candidate_groups"]["metrics"][0]["source"] == "headless_asset_document"
    assert "sql.generate" in gateway.calls


def test_real_chatbi_v1_runtime_injects_session_backed_knowledge_adapter(monkeypatch):
    captured = {}

    class CapturingRealGateway:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
            raise AssertionError("runtime 构造测试不应执行节点")

    monkeypatch.setattr(chatbi_runtime, "RealChatBICapabilityGateway", CapturingRealGateway)

    chatbi_runtime.build_real_chatbi_v1_runtime(session=object())

    knowledge_adapter = captured["knowledge_adapter"]
    assert isinstance(knowledge_adapter, HeadlessKnowledgeAdapter)
    assert knowledge_adapter._schema_builder.session is not None
