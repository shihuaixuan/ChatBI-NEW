from apps.chatbi_workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.chatbi_workflow.nodes.answer import FinishNode
from apps.chatbi_workflow.nodes.v1 import (
    ChatBIV1CapabilityNode,
    ChatBIV1InteractionNode,
)
from apps.chatbi_workflow.schemas.v1 import CHATBI_V1_OUTPUT_MODELS
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
    WorkflowPolicies,
)
from apps.workflow_engine.registry.handler_registry import HandlerRegistry


def _capability_node(name: str, handler: str) -> NodeDefinition:
    return NodeDefinition(name=name, type=NodeType.CAPABILITY, handler=handler)


def _interaction_node(name: str, handler: str) -> NodeDefinition:
    return NodeDefinition(name=name, type=NodeType.INTERACTION, handler=handler)


def build_chatbi_v1_definition() -> WorkflowDefinition:
    """构造第一版 ChatBI 业务图骨架。

    定义层只声明节点、边和路由条件，不绑定真实 ChatBI 实现，便于后续把节点能力替换成 adapter。
    """

    nodes = {
        "classify_question": _capability_node("classify_question", "question.classify"),
        "reject_answer": _capability_node("reject_answer", "answer.reject"),
        "chitchat_answer": _capability_node("chitchat_answer", "answer.chitchat"),
        "rewrite_question": _capability_node("rewrite_question", "question.rewrite"),
        "ask_rewrite_clarification": _interaction_node(
            "ask_rewrite_clarification",
            "interaction.ask_rewrite_clarification",
        ),
        "draw_image_profile": _capability_node("draw_image_profile", "question.draw_image_profile"),
        "recognize_intent": _capability_node("recognize_intent", "intent.recognize"),
        "ask_intent_clarification": _interaction_node(
            "ask_intent_clarification",
            "interaction.ask_intent_clarification",
        ),
        "ask_slot_clarification": _interaction_node(
            "ask_slot_clarification",
            "interaction.ask_slot_clarification",
        ),
        "retrieve_knowledge": _capability_node("retrieve_knowledge", "knowledge.retrieve"),
        "ask_cross_model_split": _interaction_node(
            "ask_cross_model_split",
            "interaction.ask_cross_model_split",
        ),
        "ask_metric_selection": _interaction_node("ask_metric_selection", "interaction.ask_metric_selection"),
        "generate_split_queries": _capability_node("generate_split_queries", "sql.generate_split"),
        "execute_split_queries": _capability_node("execute_split_queries", "sql.execute_split"),
        "generate_sql": _capability_node("generate_sql", "sql.generate"),
        "execute_sql": _capability_node("execute_sql", "sql.execute"),
        "handle_sql_error": _capability_node("handle_sql_error", "sql.handle_error"),
        "generate_question_answer": _capability_node("generate_question_answer", "answer.generate"),
        "recommend_questions": _capability_node("recommend_questions", "question.recommend"),
        "compose_final_reply": _capability_node("compose_final_reply", "answer.compose"),
        "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="answer.finish"),
    }
    return WorkflowDefinition(
        name="chatbi",
        version="v1",
        start_node="classify_question",
        nodes=nodes,
        edges=[
            EdgeDefinition(
                source="classify_question",
                target="reject_answer",
                condition="question.forbidden",
                priority=0,
            ),
            EdgeDefinition(
                source="classify_question",
                target="chitchat_answer",
                condition="question.chitchat",
                priority=1,
            ),
            EdgeDefinition(
                source="classify_question",
                target="rewrite_question",
                condition="question.data_or_followup",
                priority=2,
            ),
            EdgeDefinition(source="classify_question", target="rewrite_question"),
            EdgeDefinition(source="reject_answer", target="compose_final_reply"),
            EdgeDefinition(source="chitchat_answer", target="compose_final_reply"),
            EdgeDefinition(
                source="rewrite_question",
                target="ask_rewrite_clarification",
                condition="rewrite.need_user_input",
                priority=0,
            ),
            EdgeDefinition(source="rewrite_question", target="draw_image_profile"),
            EdgeDefinition(
                source="ask_rewrite_clarification",
                target="rewrite_question",
                condition="interaction.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_rewrite_clarification",
                target="generate_question_answer",
                condition="interaction.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_rewrite_clarification", target="rewrite_question"),
            EdgeDefinition(source="draw_image_profile", target="recognize_intent"),
            EdgeDefinition(
                source="recognize_intent",
                target="ask_intent_clarification",
                condition="intent.ambiguous",
                priority=0,
            ),
            EdgeDefinition(
                source="recognize_intent",
                target="ask_slot_clarification",
                condition="slot.clarification_needed",
                priority=1,
            ),
            EdgeDefinition(source="recognize_intent", target="retrieve_knowledge"),
            EdgeDefinition(
                source="ask_intent_clarification",
                target="recognize_intent",
                condition="interaction.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_intent_clarification",
                target="generate_question_answer",
                condition="interaction.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_intent_clarification", target="recognize_intent"),
            EdgeDefinition(
                source="ask_slot_clarification",
                target="retrieve_knowledge",
                condition="interaction.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_slot_clarification",
                target="generate_question_answer",
                condition="interaction.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_slot_clarification", target="retrieve_knowledge"),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_question_answer",
                condition="knowledge.missed",
                priority=0,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="ask_cross_model_split",
                condition="knowledge.cross_model",
                priority=1,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="ask_metric_selection",
                condition="knowledge.metric_ambiguous",
                priority=2,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_sql",
                condition="knowledge.hit",
                priority=3,
            ),
            EdgeDefinition(source="retrieve_knowledge", target="generate_sql"),
            EdgeDefinition(
                source="ask_cross_model_split",
                target="generate_split_queries",
                condition="cross_model.split_requested",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_cross_model_split",
                target="generate_question_answer",
                condition="interaction.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_cross_model_split", target="generate_question_answer"),
            EdgeDefinition(source="generate_split_queries", target="execute_split_queries"),
            EdgeDefinition(
                source="execute_split_queries",
                target="handle_sql_error",
                condition="sql.execution_failed",
                priority=0,
            ),
            EdgeDefinition(
                source="execute_split_queries",
                target="generate_question_answer",
                condition="sql.execution_succeeded",
                priority=1,
            ),
            EdgeDefinition(source="execute_split_queries", target="generate_question_answer"),
            EdgeDefinition(
                source="ask_metric_selection",
                target="generate_sql",
                condition="interaction.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_metric_selection",
                target="generate_question_answer",
                condition="interaction.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_metric_selection", target="generate_sql"),
            EdgeDefinition(source="generate_sql", target="execute_sql"),
            EdgeDefinition(
                source="execute_sql",
                target="handle_sql_error",
                condition="sql.execution_failed",
                priority=0,
            ),
            EdgeDefinition(
                source="execute_sql",
                target="generate_question_answer",
                condition="sql.execution_succeeded",
                priority=1,
            ),
            EdgeDefinition(source="execute_sql", target="generate_question_answer"),
            EdgeDefinition(
                source="handle_sql_error",
                target="generate_sql",
                condition="sql.error_retryable",
                priority=0,
            ),
            EdgeDefinition(source="handle_sql_error", target="generate_question_answer"),
            EdgeDefinition(source="generate_question_answer", target="recommend_questions"),
            EdgeDefinition(source="recommend_questions", target="compose_final_reply"),
            EdgeDefinition(source="compose_final_reply", target="finish"),
        ],
        input_schema={"required": ["question", "dataset_id"]},
        output_schema={"required": ["final_reply"]},
        policies=WorkflowPolicies(max_nodes_per_run=40, max_loop_iterations=3, run_timeout_ms=120_000),
        metadata={"description": "ChatBI 第一版业务流程图，占位能力可完整推进主流程与主要分支。"},
    )


def register_chatbi_v1_handlers(registry: HandlerRegistry, gateway: ChatBICapabilityGateway) -> None:
    """注册 ChatBI v1 的业务命名处理器。"""

    output_paths = {
        "question.classify": "variables.classification",
        "answer.reject": "variables.answer",
        "answer.chitchat": "variables.answer",
        "question.rewrite": "variables.rewrite",
        "question.draw_image_profile": "variables.image_profile",
        "intent.recognize": "variables.intent",
        "knowledge.retrieve": "variables.knowledge",
        "sql.generate": "variables.sql",
        "sql.generate_split": "variables.split_sql",
        "sql.execute": "variables.sql_execution",
        "sql.execute_split": "variables.sql_execution",
        "sql.handle_error": "variables.sql_error",
        "answer.generate": "variables.answer",
        "question.recommend": "variables.recommendations",
        "answer.compose": "variables.final_reply",
    }
    for capability, output_path in output_paths.items():
        registry.register(
            capability,
            ChatBIV1CapabilityNode(
                gateway,
                capability,
                output_path,
                output_model=CHATBI_V1_OUTPUT_MODELS[capability],
            ),
        )

    registry.register(
        "interaction.ask_rewrite_clarification",
        ChatBIV1InteractionNode(gateway, "interaction.ask_rewrite_clarification", "variables.rewrite_response"),
    )
    registry.register(
        "interaction.ask_intent_clarification",
        ChatBIV1InteractionNode(gateway, "interaction.ask_intent_clarification", "variables.intent_response"),
    )
    registry.register(
        "interaction.ask_slot_clarification",
        ChatBIV1InteractionNode(gateway, "interaction.ask_slot_clarification", "variables.slot_response"),
    )
    registry.register(
        "interaction.ask_cross_model_split",
        ChatBIV1InteractionNode(
            gateway,
            "interaction.ask_cross_model_split",
            "variables.cross_model_response",
        ),
    )
    registry.register(
        "interaction.ask_metric_selection",
        ChatBIV1InteractionNode(gateway, "interaction.ask_metric_selection", "variables.metric_selection"),
    )
    registry.register("answer.finish", FinishNode())
