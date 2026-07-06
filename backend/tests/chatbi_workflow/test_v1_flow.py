from datetime import datetime

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
from apps.workflow_engine.domain.interaction import InteractionRequest
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
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


def _dimension_value_issue(dimension: str) -> dict:
    return {
        "slot_type": "dimension_value",
        "dimension": dimension,
        "role": "ambiguous",
        "value_status": "not_provided",
        "reason": f"用户提到了{dimension}维度，但没有提供具体值或分组方式",
    }


def _validation(slot_issues: list[dict] | None = None) -> dict:
    issues = slot_issues or []
    return {
        "status": "valid",
        "reason_code": "INTENT_VALID",
        "repair_hint": None,
        "retryable": False,
        "retry_count": 0,
        "max_retry_count": 2,
        "violations": [],
        "clarification_required": bool(issues),
        "slot_issues": issues,
    }


class RealKnowledgeGateway(TrackingGateway):
    def __init__(self, knowledge_adapter: HeadlessKnowledgeAdapter) -> None:
        super().__init__()
        self._knowledge_adapter = knowledge_adapter

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "knowledge.retrieve":
            return self._knowledge_adapter.retrieve(request)
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class DimensionAmbiguityGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "intent.recognize":
            return {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["访问人数"],
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "not_provided",
                    }
                ],
                "time_mentions": ["今天"],
                "time_range": {"raw": "今天", "value_status": "provided"},
                "filter_mentions": [],
                "required_slot_types": ["metric"],
                "query_shape": {"select_mode": "aggregate", "needs_group_by": True},
                "ambiguous_slots": ["dimension"],
                "conflict_slots": [],
                "validation": _validation([_dimension_value_issue("店铺")]),
            }
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class MissingDimensionValueGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "intent.recognize":
            return {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["访问人数"],
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "not_provided",
                    }
                ],
                "time_mentions": ["今天"],
                "time_range": {"raw": "今天", "value_status": "provided"},
                "filter_mentions": [],
                "required_slot_types": ["metric"],
                "query_shape": {"select_mode": "aggregate"},
                "ambiguous_slots": [],
                "conflict_slots": [],
                "validation": _validation([_dimension_value_issue("店铺")]),
            }
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class SubjectDomainAmbiguityGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "intent.recognize":
            return {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["访问人数"],
                "dimension_mentions": [],
                "dimension_slots": [],
                "time_mentions": ["今天"],
                "time_range": {"raw": "今天", "value_status": "provided"},
                "filter_mentions": [],
                "required_slot_types": ["metric"],
                "query_shape": {"select_mode": "aggregate"},
                "subject_domain": {
                    "status": "ambiguous",
                    "domain_id": None,
                    "domain_name": None,
                    "domain_biz_name": None,
                    "confidence": 0.5,
                    "reason": "店铺和商品主题都可能匹配",
                    "candidate_domain_ids": [1, 2],
                },
                "ambiguous_slots": ["subject_domain"],
                "conflict_slots": [],
                "validation": _validation(
                    [
                        {
                            "slot_type": "subject_domain",
                            "reason": "主题域未能唯一确定",
                        }
                    ]
                ),
            }
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class TimeValueInDimensionGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "intent.recognize":
            return {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["访问人数"],
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
                ],
                "time_mentions": ["今天"],
                "time_range": {
                    "raw": "今天",
                    "value_status": "provided",
                    "normalized": {
                        "kind": "single_date",
                        "anchor": "today",
                        "offset_days": 0,
                        "timezone": "Asia/Shanghai",
                    },
                },
                "filter_mentions": [],
                "required_slot_types": ["metric"],
                "query_shape": {"select_mode": "aggregate"},
                "ambiguous_slots": [],
                "conflict_slots": [],
                "validation": _validation([_dimension_value_issue("店铺")]),
            }
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
        "plan.bind",
        "sql.generate",
        "sql.execute",
        "answer.generate",
        "question.recommend",
        "answer.compose",
    ]
    assert outcome.context.variables["final_reply"]["final_answer"] == "这是图工作流占位回答：最近 7 天销售额"
    assert outcome.context.variables["execution"] == outcome.context.variables["sql_execution"]
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
    assert "intent.validate" not in gateway.calls
    assert "sql.execute" in gateway.calls


def test_chatbi_v1_intent_postprocess_routes_to_slot_clarification_when_dimension_value_is_missing():
    gateway = TimeValueInDimensionGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-intent-validation-retry",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天店铺访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.WAITING_INPUT
    assert outcome.current_node == "ask_slot_clarification"
    assert gateway.calls.count("intent.recognize") == 1
    assert "intent.validate" not in gateway.calls
    assert "knowledge.retrieve" not in gateway.calls
    assert outcome.context.variables["intent"]["validation"]["clarification_required"] is True
    assert outcome.context.variables["intent"]["validation"]["slot_issues"][0]["slot_type"] == "dimension_value"
    assert outcome.context.variables["intent"]["dimension_slots"] == [
        {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
    ]


def test_chatbi_v1_dimension_ambiguity_routes_to_slot_clarification():
    gateway = DimensionAmbiguityGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-dimension-ambiguity",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天店铺的访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.WAITING_INPUT
    assert outcome.current_node == "ask_slot_clarification"
    assert "interaction.ask_intent_clarification" not in gateway.calls
    interaction = runtime._interactions.get(outcome.context.control.pending_interaction_id or "")
    assert interaction.prompt == "请确认“店铺”这个维度的使用方式。"
    assert interaction.options == [
        {"label": "按店铺分组查看", "value": {"dimension": "店铺", "dimension_usage": "group_by"}},
        {
            "label": "筛选某个具体店铺",
            "value": {
                "dimension": "店铺",
                "dimension_usage": "filter_value_required",
                "dimension_value_fields": ["店铺"],
            },
        },
        {"label": "不使用店铺维度", "value": {"dimension": "店铺", "dimension_usage": "ignore"}},
    ]


def test_chatbi_v1_missing_dimension_value_routes_to_slot_clarification():
    gateway = MissingDimensionValueGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-missing-dimension-value",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天店铺的访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.WAITING_INPUT
    assert outcome.current_node == "ask_slot_clarification"
    assert "knowledge.retrieve" not in gateway.calls
    interaction = runtime._interactions.get(outcome.context.control.pending_interaction_id or "")
    assert interaction.prompt == "请确认“店铺”这个维度的使用方式。"


def test_chatbi_v1_slot_clarification_can_resume_to_success():
    gateway = DimensionAmbiguityGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-slot-clarification",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天店铺的访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    pending_interaction_id = paused.context.control.pending_interaction_id
    outcome = runtime.resume(
        run.run_id,
        pending_interaction_id or "",
        {"dimension": "店铺", "dimension_usage": "group_by"},
        tenant_id=10,
        user_id=20,
    )

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.current_node == "finish"
    assert "interaction.ask_intent_clarification" not in gateway.calls
    assert gateway.calls.index("knowledge.retrieve") > gateway.calls.index("intent.recognize")
    assert gateway.calls.index("knowledge.retrieve") > gateway.calls.index("interaction.ask_slot_clarification")
    assert outcome.context.variables["intent"]["ambiguous_slots"] == []
    assert outcome.context.variables["intent"]["dimension_slots"] == [
        {"name": "店铺", "role": "group_by", "value": None, "value_status": "not_provided"}
    ]


def test_chatbi_v1_subject_domain_clarification_can_resume_to_knowledge():
    gateway = SubjectDomainAmbiguityGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-subject-domain-clarification",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    pending_interaction_id = paused.context.control.pending_interaction_id
    outcome = runtime.resume(
        run.run_id,
        pending_interaction_id or "",
        {"subject_domain": "商品", "domain_id": 2},
        tenant_id=10,
        user_id=20,
    )

    assert paused.status is RunStatus.WAITING_INPUT
    assert paused.current_node == "ask_slot_clarification"
    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls.index("knowledge.retrieve") > gateway.calls.index("interaction.ask_slot_clarification")
    assert outcome.context.variables["intent"]["ambiguous_slots"] == []
    assert outcome.context.variables["intent"]["subject_domain"] == {
        "status": "selected",
        "domain_id": 2,
        "domain_name": "商品",
        "domain_biz_name": None,
        "confidence": 1.0,
        "reason": "用户已确认主题域",
        "candidate_domain_ids": [2],
    }


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


def test_metric_selection_patcher_prunes_non_query_dimensions_for_plain_metric_query():
    now = datetime.now()
    run = WorkflowRun(
        run_id="run-1",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="digest",
        context=WorkflowContext(
            request={"question": "今天店铺1的线上客户数", "dataset_id": 3, "tenant_id": 1, "user_id": 1},
            variables={
                "intent": {
                    "intent_type": "metric_query",
                    "query_shape": {"needs_group_by": False},
                    "dimension_slots": [{"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}],
                },
                "knowledge": {
                    "status": "metric_ambiguous",
                    "ambiguities": [
                        {
                            "type": "metric",
                            "candidates": [
                                {
                                    "asset_id": 239,
                                    "biz_name": "total_customer_cnt_online",
                                    "name": "总客户数-线上（累计）",
                                }
                            ],
                        }
                    ],
                    "candidate_groups": {
                        "metrics": [
                            {
                                "asset_id": 239,
                                "biz_name": "total_customer_cnt_online",
                                "name": "总客户数-线上（累计）",
                            }
                        ]
                    },
                    "selected_assets": {
                        "metrics": [],
                        "dimensions": [
                            {"asset_id": 254, "biz_name": "stall_id", "name": "店铺ID "},
                            {"asset_id": 255, "biz_name": "top10_contrib_customers", "name": "Top 10 贡献客户"},
                            {"asset_id": 252, "biz_name": "stat_date", "name": "时间"},
                        ],
                    },
                    "slot_bindings": {
                        "metrics": [],
                        "dimensions": [
                            {"asset_id": 254, "biz_name": "stall_id", "display_name": "店铺ID "},
                            {"asset_id": 255, "biz_name": "top10_contrib_customers", "display_name": "Top 10 贡献客户"},
                            {"asset_id": 252, "biz_name": "stat_date", "display_name": "时间"},
                        ],
                        "filters": [
                            {"asset_id": 254, "biz_name": "stall_id", "operator": "=", "value": "1"},
                            {
                                "asset_id": 252,
                                "biz_name": "stat_date",
                                "operator": "=",
                                "value": {"kind": "relative_date", "value": "today"},
                            },
                        ],
                    },
                },
            },
        ),
        created_at=now,
        updated_at=now,
    )
    interaction = InteractionRequest(
        interaction_id="interaction-1",
        run_id="run-1",
        node_name="ask_metric_selection",
        response_schema={},
        created_at=now,
    )

    patch = chatbi_runtime.ChatBIV1InteractionResponsePatcher()(run, interaction, {"metric": 239})

    knowledge = patch.set_values["variables.knowledge"]
    assert knowledge["selected_assets"]["metrics"] == [
        {
            "asset_id": 239,
            "biz_name": "total_customer_cnt_online",
            "display_name": "总客户数-线上（累计）",
            "source": "user_selected",
        }
    ]
    assert [item["asset_id"] for item in knowledge["selected_assets"]["dimensions"]] == [254, 252]
    assert [item["asset_id"] for item in knowledge["slot_bindings"]["dimensions"]] == [254, 252]
    assert knowledge["dimensions"] == ["stall_id", "stat_date"]


def test_metric_selection_patcher_keeps_only_group_dimension_from_selected_metric_model():
    selected_assets = {
        "metrics": [],
        "dimensions": [
            {
                "asset_id": 278,
                "model_id": 246,
                "biz_name": "stall_id",
                "name": "档口ID",
                "payload": {"alias": ["档口"], "ext_info": {}},
            },
            {
                "asset_id": 296,
                "model_id": 248,
                "biz_name": "stall_id",
                "name": "档口ID",
                "payload": {"alias": ["档口"], "ext_info": {}},
            },
            {
                "asset_id": 277,
                "model_id": 246,
                "biz_name": "seller_id",
                "name": "商家ID",
                "payload": {"ext_info": {}},
            },
        ],
    }
    slot_bindings = {
        "dimensions": [
            {"asset_id": 278, "biz_name": "stall_id"},
            {"asset_id": 296, "biz_name": "stall_id"},
            {"asset_id": 277, "biz_name": "seller_id"},
        ],
        "filters": [],
    }
    selected_assets["metrics"] = [{"asset_id": 269, "model_id": 246, "biz_name": "gmv_sale"}]

    assets, bindings = chatbi_runtime.ChatBIV1InteractionResponsePatcher._prune_dimensions_after_metric_selection(
        selected_assets,
        slot_bindings,
        {
            "query_shape": {"needs_group_by": True},
            "dimension_slots": [{"name": "档口", "role": "group_by", "value_status": "not_provided"}],
        },
    )

    assert [item["asset_id"] for item in assets["dimensions"]] == [278]
    assert [item["asset_id"] for item in bindings["dimensions"]] == [278]


def test_metric_selection_patcher_preserves_selected_metric_model_id():
    patcher = chatbi_runtime.ChatBIV1InteractionResponsePatcher()

    asset = patcher._metric_asset(
        {
            "asset_id": 280,
            "model_id": 248,
            "biz_name": "stock_qty",
            "name": "当前库存件数",
            "payload": {"fields": ["stock_qty"]},
        },
        280,
    )

    assert asset["model_id"] == 248
    assert asset["payload"] == {"fields": ["stock_qty"]}


def test_slot_clarification_patcher_uses_structured_dimension_values():
    now = datetime.now()
    run = WorkflowRun(
        run_id="run-slot-values",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="digest",
        context=WorkflowContext(
            request={"question": "今天店铺的客户数是多少", "dataset_id": 3, "tenant_id": 1, "user_id": 1},
            variables={
                "intent": {
                    "intent_type": "metric_query",
                    "ambiguous_slots": ["dimension"],
                    "dimension_mentions": ["店铺"],
                    "dimension_slots": [
                        {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"}
                    ],
                },
            },
        ),
        created_at=now,
        updated_at=now,
    )
    interaction = InteractionRequest(
        interaction_id="interaction-slot-values",
        run_id="run-slot-values",
        node_name="ask_slot_clarification",
        response_schema={},
        created_at=now,
    )

    patch = chatbi_runtime.ChatBIV1InteractionResponsePatcher()(
        run,
        interaction,
        {"dimension_usage": "filter_value_required", "dimension_values": {"店铺": "店铺为1"}},
    )

    intent = patch.set_values["variables.intent"]
    assert intent["ambiguous_slots"] == []
    assert intent["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}
    ]


def test_slot_clarification_patcher_accepts_multiple_dimension_values():
    now = datetime.now()
    run = WorkflowRun(
        run_id="run-multi-slot-values",
        definition_name="chatbi",
        definition_version="v1",
        definition_digest="digest",
        context=WorkflowContext(
            request={"question": "今天店铺商品的客户数是多少", "dataset_id": 3, "tenant_id": 1, "user_id": 1},
            variables={
                "intent": {
                    "intent_type": "metric_query",
                    "ambiguous_slots": ["dimension"],
                    "dimension_mentions": ["店铺", "商品"],
                    "dimension_slots": [
                        {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"},
                        {"name": "商品", "role": "filter", "value": None, "value_status": "not_provided"},
                    ],
                },
            },
        ),
        created_at=now,
        updated_at=now,
    )
    interaction = InteractionRequest(
        interaction_id="interaction-multi-slot-values",
        run_id="run-multi-slot-values",
        node_name="ask_slot_clarification",
        response_schema={},
        created_at=now,
    )

    patch = chatbi_runtime.ChatBIV1InteractionResponsePatcher()(
        run,
        interaction,
        {
            "dimension_usage": "filter_value_required",
            "dimension_values": {"店铺": "1", "商品": "A100"},
        },
    )

    intent = patch.set_values["variables.intent"]
    assert intent["ambiguous_slots"] == []
    assert intent["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"},
        {"name": "商品", "role": "filter", "value": "A100", "value_status": "provided"},
    ]


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
