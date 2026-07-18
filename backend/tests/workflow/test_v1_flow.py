from types import SimpleNamespace
from typing import Any, cast

from apps.retrieval.service import RetrievalService
from apps.workflow import runtime as chatbi_runtime
from apps.workflow.capabilities.adapters.knowledge import (
    SemanticKnowledgeAdapter,
)
from apps.workflow.capabilities.interactions import (
    apply_slot_response_to_intent,
    prune_dimensions_for_selected_metric,
    selected_metric_from_response,
)
from apps.workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.workflow.capabilities.planning import QueryPlanBinder
from apps.workflow.conditions.core import register_chatbi_conditions
from apps.workflow.definitions.chatbi_v1 import (
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


class FakeRetrievalService:
    """记录 Workflow 发出的统一检索请求，并返回当前语义载荷。"""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def retrieve(self, request):
        self.requests.append(request)
        dimension_filters = [
            {
                "asset_type": "DIMENSION",
                "asset_id": 200,
                "name": slot.name,
                "biz_name": "stall_id",
                "operator": "=",
                "value": slot.value,
            }
            for slot in request.intent.dimension_slots
            if slot.role == "filter" and slot.value_status == "provided"
        ]
        metric = {
            "asset_type": "METRIC",
            "asset_id": 100,
            "model_id": 10,
            "name": "访问量",
            "biz_name": "visit_uv",
            "source": "semantic_binding",
        }
        return SimpleNamespace(
            payload={
                "hit": True,
                "status": "hit",
                "candidate_groups": {
                    "metrics": [metric],
                    "dimensions": [],
                    "values": [],
                    "terms": [],
                },
                "selected_assets": {
                    "metrics": [metric],
                    "dimensions": [],
                    "values": [],
                    "terms": [],
                },
                "slot_bindings": {
                    "metrics": [metric],
                    "group_dimensions": [],
                    "dimension_filters": dimension_filters,
                    "value_filters": [],
                    "time_filters": [],
                },
            }
        )


def _knowledge_adapter(service: FakeRetrievalService) -> SemanticKnowledgeAdapter:
    # 测试替身只实现适配器使用的 retrieve 协议。
    return SemanticKnowledgeAdapter(cast(RetrievalService, service))


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
    def __init__(self, knowledge_adapter: SemanticKnowledgeAdapter) -> None:
        super().__init__()
        self._knowledge_adapter = knowledge_adapter

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "knowledge.retrieve":
            return self._knowledge_adapter.retrieve(request)
        return PlaceholderChatBICapabilityGateway.invoke(self, capability, request, idempotency_key)


class MetricSelectionPlanningGateway(TrackingGateway):
    def __init__(self) -> None:
        super().__init__()
        self._plan_binder = QueryPlanBinder()

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "plan.bind":
            return self._plan_binder.bind(request)
        self.calls.pop()
        return super().invoke(capability, request, idempotency_key)


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


class DimensionAmbiguityKnowledgePlanningGateway(DimensionAmbiguityGateway):
    def __init__(self, knowledge_adapter: SemanticKnowledgeAdapter) -> None:
        super().__init__()
        self._knowledge_adapter = knowledge_adapter
        self._plan_binder = QueryPlanBinder()

    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "knowledge.retrieve":
            return self._knowledge_adapter.retrieve(request)
        if capability == "plan.bind":
            return self._plan_binder.bind(request)
        self.calls.pop()
        return super().invoke(capability, request, idempotency_key)


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


class EmptyResultGateway(TrackingGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        self.calls.append(capability)
        if capability == "sql.execute":
            return {
                "status": "succeeded",
                "rows": [],
                "row_count": 0,
                "fields": ["visit_uv"],
                "execution_ms": 1,
            }
        if capability == "execution.validate":
            return {
                "status": "empty",
                "issues": [
                    {
                        "type": "empty_result",
                        "query_id": "query-0",
                        "message": "查询成功但没有返回数据",
                    }
                ],
                "suggestions": ["可以尝试放宽筛选条件或调整时间范围"],
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
        "execution.validate",
        "answer.generate",
        "question.recommend",
        "answer.compose",
    ]
    assert outcome.context.variables["final_reply"]["final_answer"] == "这是图工作流占位回答：最近 7 天销售额"
    assert outcome.context.variables["execution"] == outcome.context.variables["sql_execution"]
    assert outcome.context.variables["completed"] is True


def test_chatbi_v1_empty_sql_result_is_validated_before_answer():
    gateway = EmptyResultGateway()
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-empty-result-validation",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天访问人数", "dataset_id": 1, "tenant_id": 10, "user_id": 20}),
    )

    outcome = runtime.execute(run.run_id)

    assert outcome.status is RunStatus.SUCCEEDED
    assert gateway.calls.index("execution.validate") > gateway.calls.index("sql.execute")
    assert gateway.calls.index("answer.generate") > gateway.calls.index("execution.validate")
    assert outcome.context.variables["execution"]["validation"]["status"] == "empty"
    assert outcome.context.variables["execution"]["validation"]["issues"][0]["type"] == "empty_result"


def test_chatbi_v1_runtime_uses_plain_interaction_resume():
    runtime = chatbi_runtime.build_placeholder_chatbi_v1_runtime(session=object())

    legacy_attr = "_interaction_response_" + "pat" + "cher"
    assert legacy_attr not in vars(runtime)


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
    assert outcome.context.variables["slot_response"] == {"dimension": "店铺", "dimension_usage": "group_by"}
    assert outcome.context.variables["interactions"]["ask_slot_clarification"]["response"] == {
        "dimension": "店铺",
        "dimension_usage": "group_by",
    }
    assert outcome.context.variables["intent"]["ambiguous_slots"] == ["dimension"]
    assert outcome.context.variables["intent"]["dimension_slots"] == [
        {"name": "店铺", "role": "ambiguous", "value": None, "value_status": "not_provided"}
    ]


def test_chatbi_v1_slot_clarification_filter_response_feeds_knowledge_and_plan():
    retrieval_service = FakeRetrievalService()
    knowledge_adapter = _knowledge_adapter(retrieval_service)
    gateway = DimensionAmbiguityKnowledgePlanningGateway(knowledge_adapter)
    runtime = _runtime(gateway)
    run = runtime.create_run(
        "chatbi-v1-slot-clarification-filter",
        "chatbi",
        "v1",
        WorkflowContext(request={"question": "今天店铺的访问人数", "dataset_id": 20, "tenant_id": 10, "user_id": 20}),
    )

    paused = runtime.execute(run.run_id)
    outcome = runtime.resume(
        run.run_id,
        paused.context.control.pending_interaction_id or "",
        {"dimension_usage": "filter_value_required", "dimension_values": {"店铺": "店铺为1"}},
        tenant_id=10,
        user_id=20,
    )

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.context.variables["slot_response"]["dimension_values"] == {"店铺": "店铺为1"}
    assert outcome.context.variables["interactions"]["ask_slot_clarification"]["response"]["dimension_values"] == {
        "店铺": "店铺为1"
    }
    assert outcome.context.variables["intent"]["dimension_slots"][0]["value"] is None
    request_intent = retrieval_service.requests[0].intent
    assert request_intent.dimension_slots[0].value == "1"
    assert outcome.context.variables["knowledge"]["slot_bindings"]["dimension_filters"][0]["value"] == "1"
    assert any(item.get("value") == "1" for item in outcome.context.variables["plan"]["filters"])


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
    assert outcome.context.variables["slot_response"] == {"subject_domain": "商品", "domain_id": 2}
    assert outcome.context.variables["interactions"]["ask_slot_clarification"]["response"] == {
        "subject_domain": "商品",
        "domain_id": 2,
    }
    assert outcome.context.variables["intent"]["ambiguous_slots"] == ["subject_domain"]
    assert outcome.context.variables["intent"]["subject_domain"]["status"] == "ambiguous"


def test_chatbi_v1_placeholder_metric_selection_can_resume_to_success():
    gateway = MetricSelectionPlanningGateway()
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
    assert resumed.context.variables["metric_selection"] == {"metric": "sales_amount"}
    assert resumed.context.variables["interactions"]["ask_metric_selection"]["response"] == {
        "metric": "sales_amount"
    }
    assert gateway.calls.count("knowledge.retrieve") == 1
    assert gateway.calls.index("sql.generate") > gateway.calls.index("interaction.ask_metric_selection")
    assert "sql.execute" in gateway.calls
    knowledge = resumed.context.variables["knowledge"]
    assert knowledge["status"] == "metric_ambiguous"
    assert knowledge["ambiguities"] == [{"type": "metric", "candidates": ["sales_amount", "gross_profit"]}]
    plan = resumed.context.variables["plan"]
    assert plan["status"] == "ready"
    assert plan["metrics"][0]["asset_id"] == "sales_amount"


def test_metric_selection_plan_keeps_plain_metric_query_filter_only_dimensions():
    knowledge = {
        "hit": True,
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
    }
    variables = {
        "intent": {
            "intent_type": "metric_query",
            "query_shape": {"needs_group_by": False},
            "dimension_slots": [{"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}],
        },
        "knowledge": knowledge,
        "interactions": {
            "ask_metric_selection": {
                "node_name": "ask_metric_selection",
                "round": 1,
                "response": {"metric": 239},
                "skipped": False,
            }
        },
    }

    plan = QueryPlanBinder().bind({"request": {"question": "今天店铺1的线上客户数"}, "variables": variables})

    assert plan["status"] == "ready"
    assert plan["metrics"][0]["asset_id"] == 239
    assert plan["group_bys"] == []
    assert [item["asset_id"] for item in plan["filters"]] == [254, 252]
    assert knowledge["selected_assets"]["metrics"] == []


def test_metric_selection_prunes_group_dimension_to_selected_metric_model():
    selected_assets = {
        "metrics": [{"asset_id": 269, "model_id": 246, "biz_name": "gmv_sale"}],
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
    slots = {
        "dimensions": [
            {"asset_id": 278, "display_name": "档口ID", "biz_name": "stall_id"},
            {"asset_id": 296, "display_name": "档口ID", "biz_name": "stall_id"},
            {"asset_id": 277, "display_name": "商家ID", "biz_name": "seller_id"},
        ],
        "filters": [],
    }

    dimensions, filters = prune_dimensions_for_selected_metric(
        selected_assets,
        slots,
        {
            "query_shape": {"needs_group_by": True},
            "dimension_slots": [{"name": "档口", "role": "group_by", "value_status": "not_provided"}],
        },
    )

    assert [item["asset_id"] for item in dimensions] == [278]
    assert filters == []
    assert [item["asset_id"] for item in slot_bindings["dimensions"]] == [278, 296, 277]


def test_metric_selection_helper_preserves_selected_metric_model_id():
    asset = selected_metric_from_response(
        {
            "hit": True,
            "candidate_groups": {
                "metrics": [
                    {
                        "asset_id": 280,
                        "model_id": 248,
                        "biz_name": "stock_qty",
                        "name": "当前库存件数",
                        "payload": {"fields": ["stock_qty"]},
                    }
                ]
            },
        },
        {"metric": 280},
    )

    assert asset["model_id"] == 248
    assert asset["payload"] == {"fields": ["stock_qty"]}


def test_slot_interaction_response_uses_structured_dimension_values():
    intent = {
        "intent_type": "metric_query",
        "ambiguous_slots": ["dimension"],
        "dimension_mentions": ["店铺"],
        "dimension_slots": [
            {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"}
        ],
    }

    updated = apply_slot_response_to_intent(
        intent,
        {"dimension_usage": "filter_value_required", "dimension_values": {"店铺": "店铺为1"}},
    )

    assert updated["ambiguous_slots"] == []
    assert updated["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"}
    ]
    assert intent["dimension_slots"][0]["value"] is None


def test_slot_interaction_response_accepts_multiple_dimension_values():
    intent = {
        "intent_type": "metric_query",
        "ambiguous_slots": ["dimension"],
        "dimension_mentions": ["店铺", "商品"],
        "dimension_slots": [
            {"name": "店铺", "role": "filter", "value": None, "value_status": "not_provided"},
            {"name": "商品", "role": "filter", "value": None, "value_status": "not_provided"},
        ],
    }

    updated = apply_slot_response_to_intent(
        intent,
        {
            "dimension_usage": "filter_value_required",
            "dimension_values": {"店铺": "1", "商品": "A100"},
        },
    )

    assert updated["ambiguous_slots"] == []
    assert updated["dimension_slots"] == [
        {"name": "店铺", "role": "filter", "value": "1", "value_status": "provided"},
        {"name": "商品", "role": "filter", "value": "A100", "value_status": "provided"},
    ]


def test_chatbi_v1_graph_passes_user_question_to_unified_retrieval_node():
    retrieval_service = FakeRetrievalService()
    gateway = RealKnowledgeGateway(_knowledge_adapter(retrieval_service))
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
    assert retrieval_service.requests[0].original_question == "今日店铺流量"
    assert retrieval_service.requests[0].scope.dataset_ids == [20]
    assert knowledge["status"] == "hit"
    assert knowledge["selected_assets"]["metrics"][0]["asset_id"] == 100
    assert knowledge["selected_assets"]["metrics"][0]["biz_name"] == "visit_uv"
    assert knowledge["candidate_groups"]["metrics"][0]["source"] == "semantic_binding"
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
    assert isinstance(knowledge_adapter, SemanticKnowledgeAdapter)
    assert knowledge_adapter._retrieval_service is not None
