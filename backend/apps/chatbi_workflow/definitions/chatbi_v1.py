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
        "bind_query_plan": _capability_node("bind_query_plan", "plan.bind"),
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
            # node.degraded：能力节点业务失败时优先转入解释性回答（A3）。
            EdgeDefinition(source="classify_question", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="classify_question",
                target="reject_answer",
                condition="question.forbidden",
                priority=1,
            ),
            EdgeDefinition(
                source="classify_question",
                target="chitchat_answer",
                condition="question.chitchat",
                priority=2,
            ),
            EdgeDefinition(
                source="classify_question",
                target="rewrite_question",
                condition="question.data_or_followup",
                priority=3,
            ),
            EdgeDefinition(source="classify_question", target="rewrite_question"),
            EdgeDefinition(source="reject_answer", target="compose_final_reply"),
            EdgeDefinition(source="chitchat_answer", target="compose_final_reply"),
            EdgeDefinition(source="rewrite_question", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="rewrite_question",
                target="ask_rewrite_clarification",
                condition="clarify.rewrite.allowed",
                priority=1,
            ),
            EdgeDefinition(
                source="rewrite_question",
                target="generate_question_answer",
                condition="clarify.rewrite.exhausted",
                priority=2,
            ),
            EdgeDefinition(source="rewrite_question", target="draw_image_profile"),
            EdgeDefinition(
                source="ask_rewrite_clarification",
                target="rewrite_question",
                condition="interaction.rewrite.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_rewrite_clarification",
                target="generate_question_answer",
                condition="interaction.rewrite.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_rewrite_clarification", target="rewrite_question"),
            EdgeDefinition(source="draw_image_profile", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(source="draw_image_profile", target="recognize_intent"),
            EdgeDefinition(source="recognize_intent", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="recognize_intent",
                target="ask_intent_clarification",
                condition="clarify.intent.allowed",
                priority=1,
            ),
            EdgeDefinition(
                source="recognize_intent",
                target="generate_question_answer",
                condition="clarify.intent.exhausted",
                priority=2,
            ),
            EdgeDefinition(
                source="recognize_intent",
                target="ask_slot_clarification",
                condition="clarify.slot.allowed",
                priority=3,
            ),
            EdgeDefinition(
                source="recognize_intent",
                target="generate_question_answer",
                condition="clarify.slot.exhausted",
                priority=4,
            ),
            EdgeDefinition(source="recognize_intent", target="retrieve_knowledge"),
            EdgeDefinition(
                source="ask_intent_clarification",
                target="recognize_intent",
                condition="interaction.intent.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_intent_clarification",
                target="generate_question_answer",
                condition="interaction.intent.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_intent_clarification", target="recognize_intent"),
            EdgeDefinition(
                source="ask_slot_clarification",
                target="retrieve_knowledge",
                condition="interaction.slot.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_slot_clarification",
                target="generate_question_answer",
                condition="interaction.slot.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_slot_clarification", target="retrieve_knowledge"),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_question_answer",
                condition="node.degraded",
                priority=0,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_question_answer",
                condition="knowledge.missed",
                priority=1,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="ask_cross_model_split",
                condition="clarify.cross_model.allowed",
                priority=2,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_question_answer",
                condition="clarify.cross_model.exhausted",
                priority=3,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="ask_metric_selection",
                condition="clarify.metric.allowed",
                priority=4,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="generate_question_answer",
                condition="clarify.metric.exhausted",
                priority=5,
            ),
            EdgeDefinition(
                source="retrieve_knowledge",
                target="bind_query_plan",
                condition="knowledge.hit",
                priority=6,
            ),
            EdgeDefinition(source="retrieve_knowledge", target="bind_query_plan"),
            # 查询计划绑定：检索证据在此收敛为 SQL 生成的唯一事实源。
            EdgeDefinition(source="bind_query_plan", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="bind_query_plan",
                target="generate_question_answer",
                condition="plan.infeasible",
                priority=1,
            ),
            EdgeDefinition(source="bind_query_plan", target="generate_sql"),
            EdgeDefinition(
                source="ask_cross_model_split",
                target="generate_split_queries",
                condition="cross_model.split_requested",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_cross_model_split",
                target="generate_question_answer",
                condition="interaction.cross_model.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_cross_model_split", target="generate_question_answer"),
            EdgeDefinition(source="generate_split_queries", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(source="generate_split_queries", target="execute_split_queries"),
            EdgeDefinition(source="execute_split_queries", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="execute_split_queries",
                target="handle_sql_error",
                condition="sql.execution_failed",
                priority=1,
            ),
            EdgeDefinition(
                source="execute_split_queries",
                target="generate_question_answer",
                condition="sql.execution_succeeded",
                priority=2,
            ),
            EdgeDefinition(source="execute_split_queries", target="generate_question_answer"),
            EdgeDefinition(
                source="ask_metric_selection",
                target="bind_query_plan",
                condition="interaction.metric.answered",
                priority=0,
            ),
            EdgeDefinition(
                source="ask_metric_selection",
                target="generate_question_answer",
                condition="interaction.metric.skipped",
                priority=1,
            ),
            EdgeDefinition(source="ask_metric_selection", target="bind_query_plan"),
            EdgeDefinition(source="generate_sql", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(source="generate_sql", target="execute_sql"),
            EdgeDefinition(source="execute_sql", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="execute_sql",
                target="handle_sql_error",
                condition="sql.execution_failed",
                priority=1,
            ),
            EdgeDefinition(
                source="execute_sql",
                target="generate_question_answer",
                condition="sql.execution_succeeded",
                priority=2,
            ),
            EdgeDefinition(source="execute_sql", target="generate_question_answer"),
            EdgeDefinition(source="handle_sql_error", target="generate_question_answer", condition="node.degraded", priority=0),
            EdgeDefinition(
                source="handle_sql_error",
                target="generate_sql",
                condition="sql.error_retryable",
                priority=1,
            ),
            EdgeDefinition(source="handle_sql_error", target="generate_question_answer"),
            EdgeDefinition(source="generate_question_answer", target="recommend_questions"),
            EdgeDefinition(source="recommend_questions", target="compose_final_reply"),
            EdgeDefinition(source="compose_final_reply", target="finish"),
        ],
        input_schema={"required": ["question", "dataset_id"]},
        output_schema={"required": ["final_reply"]},
        # max_loop_iterations 只是防死循环安全网；合法澄清轮次由 clarify.*.allowed/exhausted
        # 条件按交互节点单独门控（默认每个澄清点最多 2 轮）。
        policies=WorkflowPolicies(max_nodes_per_run=40, max_loop_iterations=8, run_timeout_ms=120_000),
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
        "plan.bind": "variables.plan",
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
