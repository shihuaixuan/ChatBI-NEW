from apps.chatbi.orchestration.graph.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.conditions.core import register_chatbi_conditions
from apps.chatbi.orchestration.graph.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.registry.workflow_registry import WorkflowRegistry


def test_chatbi_v1_definition_publishes_with_business_nodes_and_route_conditions():
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_v1_handlers(handlers, PlaceholderChatBICapabilityGateway())
    register_chatbi_conditions(conditions)

    definition = build_chatbi_v1_definition()
    published = WorkflowRegistry(DefinitionValidator(handlers, conditions)).publish(definition)

    assert published.name == "chatbi"
    assert published.version == "v1"
    assert definition.start_node == "classify_question"
    assert list(definition.nodes) == [
        "classify_question",
        "reject_answer",
        "chitchat_answer",
        "rewrite_question",
        "draw_image_profile",
        "recognize_intent",
        "ask_intent_clarification",
        "ask_slot_clarification",
        "retrieve_knowledge",
        "ask_cross_model_split",
        "ask_metric_selection",
        "bind_query_plan",
        "generate_split_queries",
        "execute_split_queries",
        "validate_result",
        "generate_sql",
        "execute_sql",
        "handle_sql_error",
        "generate_question_answer",
        "recommend_questions",
        "compose_final_reply",
        "finish",
    ]
    assert {edge.condition for edge in definition.edges if edge.condition} == {
        "question.forbidden",
        "question.chitchat",
        "question.data_or_followup",
        # 能力节点业务失败时优先转入解释性回答，而不是终止整个 Run。
        "node.degraded",
        # 澄清入口带轮次门控：轮次内继续提问，用尽后转兜底回答。
        "clarify.intent.allowed",
        "clarify.intent.exhausted",
        "clarify.slot.allowed",
        "clarify.slot.exhausted",
        "clarify.metric.allowed",
        "clarify.metric.exhausted",
        "clarify.cross_model.allowed",
        "clarify.cross_model.exhausted",
        # 交互出口只消费本节点自己的回答，避免残留回答串扰路由。
        "interaction.intent.answered",
        "interaction.intent.skipped",
        "interaction.slot.answered",
        "interaction.slot.temporal_answered",
        "interaction.slot.skipped",
        "interaction.metric.answered",
        "interaction.metric.skipped",
        "interaction.cross_model.skipped",
        "knowledge.missed",
        "knowledge.hit",
        # 查询计划不可行时不进入 SQL 生成，转解释性回答。
        "plan.infeasible",
        "plan.multi_query",
        "result.empty",
        "result.suspicious",
        "cross_model.split_requested",
        "sql.execution_failed",
        "sql.execution_succeeded",
        "sql.error_retryable",
    }


def test_interaction_nodes_declare_standard_and_legacy_paths():
    definition = build_chatbi_v1_definition()

    metadata = definition.nodes["ask_metric_selection"].metadata["interaction"]

    assert metadata["standard_path"] == "variables.interactions.ask_metric_selection"
    assert metadata["legacy_path"] == "variables.metric_selection"
    assert metadata["response_key"] == "metric_selection"
    assert metadata["max_rounds"] == 2
