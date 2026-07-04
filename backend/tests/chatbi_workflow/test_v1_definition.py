from apps.chatbi_workflow.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi_workflow.conditions.core import register_chatbi_conditions
from apps.chatbi_workflow.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
    register_chatbi_v1_handlers,
)
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.registry.definition_validator import DefinitionValidator
from apps.workflow_engine.registry.handler_registry import HandlerRegistry
from apps.workflow_engine.registry.workflow_registry import WorkflowRegistry


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
        "ask_rewrite_clarification",
        "draw_image_profile",
        "recognize_intent",
        "ask_intent_clarification",
        "ask_slot_clarification",
        "retrieve_knowledge",
        "ask_cross_model_split",
        "ask_metric_selection",
        "generate_split_queries",
        "execute_split_queries",
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
        "rewrite.need_user_input",
        "intent.ambiguous",
        "slot.clarification_needed",
        "knowledge.missed",
        "knowledge.metric_ambiguous",
        "knowledge.cross_model",
        "knowledge.hit",
        "interaction.answered",
        "interaction.skipped",
        "cross_model.split_requested",
        "sql.execution_failed",
        "sql.execution_succeeded",
        "sql.error_retryable",
    }
