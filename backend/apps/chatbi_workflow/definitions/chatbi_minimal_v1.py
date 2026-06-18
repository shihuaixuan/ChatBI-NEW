from apps.chatbi_workflow.capabilities.gateway import ChatBICapabilityGateway
from apps.chatbi_workflow.nodes.answer import AnswerGenerateNode, FinishNode
from apps.chatbi_workflow.nodes.evidence import SchemaRetrieveNode
from apps.chatbi_workflow.nodes.query import QueryUnderstandNode
from apps.chatbi_workflow.nodes.sql import (
    PermissionApplyNode,
    SqlExecuteNode,
    SqlGenerateNode,
    SqlValidateNode,
)
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
    WorkflowPolicies,
)
from apps.workflow_engine.registry.handler_registry import HandlerRegistry


def build_chatbi_minimal_definition() -> WorkflowDefinition:
    """构造最小 ChatBI 问数图。

    图中 SQL 校验和权限应用是执行 SQL 的前置节点，且默认失败分支会直接进入答案节点，
    从结构上保证权限拒绝或 SQL 校验失败时不会调用 `sql.execute`。
    """

    nodes = {
        "understand_question": NodeDefinition(
            name="understand_question",
            type=NodeType.CAPABILITY,
            handler="query.understand",
        ),
        "retrieve_schema": NodeDefinition(
            name="retrieve_schema",
            type=NodeType.CAPABILITY,
            handler="schema.retrieve",
        ),
        "generate_sql": NodeDefinition(
            name="generate_sql",
            type=NodeType.CAPABILITY,
            handler="sql.generate",
        ),
        "validate_sql": NodeDefinition(
            name="validate_sql",
            type=NodeType.CAPABILITY,
            handler="sql.validate",
        ),
        "apply_permission": NodeDefinition(
            name="apply_permission",
            type=NodeType.CAPABILITY,
            handler="permission.apply",
        ),
        "execute_sql": NodeDefinition(
            name="execute_sql",
            type=NodeType.CAPABILITY,
            handler="sql.execute",
        ),
        "generate_answer": NodeDefinition(
            name="generate_answer",
            type=NodeType.CAPABILITY,
            handler="answer.generate",
        ),
        "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="answer.finish"),
    }
    return WorkflowDefinition(
        name="chatbi",
        version="minimal-v1",
        start_node="understand_question",
        nodes=nodes,
        edges=[
            EdgeDefinition(source="understand_question", target="retrieve_schema"),
            EdgeDefinition(source="retrieve_schema", target="generate_sql"),
            EdgeDefinition(source="generate_sql", target="validate_sql"),
            EdgeDefinition(source="validate_sql", target="apply_permission", condition="sql.valid", priority=0),
            EdgeDefinition(source="validate_sql", target="generate_answer"),
            EdgeDefinition(source="apply_permission", target="execute_sql", condition="permission.allowed", priority=0),
            EdgeDefinition(source="apply_permission", target="generate_answer"),
            EdgeDefinition(source="execute_sql", target="generate_answer"),
            EdgeDefinition(source="generate_answer", target="finish"),
        ],
        input_schema={"required": ["question", "datasource_id"]},
        output_schema={"required": ["answer"]},
        policies=WorkflowPolicies(max_nodes_per_run=20, max_loop_iterations=2, run_timeout_ms=60_000),
    )


def register_chatbi_minimal_handlers(registry: HandlerRegistry, gateway: ChatBICapabilityGateway) -> None:
    """注册最小图的节点处理器。"""

    registry.register("query.understand", QueryUnderstandNode(gateway))
    registry.register("schema.retrieve", SchemaRetrieveNode(gateway))
    registry.register("sql.generate", SqlGenerateNode(gateway))
    registry.register("sql.validate", SqlValidateNode(gateway))
    registry.register("permission.apply", PermissionApplyNode(gateway))
    registry.register("sql.execute", SqlExecuteNode(gateway))
    registry.register("answer.generate", AnswerGenerateNode(gateway))
    registry.register("answer.finish", FinishNode())
