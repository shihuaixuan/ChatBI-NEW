from apps.chatbi.orchestration.graph.capabilities.gateway import ChatBICapabilityGateway
from apps.chatbi.orchestration.graph.nodes.answer import AnswerGenerateNode, FinishNode
from apps.chatbi.orchestration.graph.nodes.evidence import SchemaRetrieveNode
from apps.chatbi.orchestration.graph.nodes.query import QueryUnderstandNode
from apps.chatbi.orchestration.graph.nodes.sql import (
    PermissionApplyNode,
    SqlExecuteNode,
    SqlGenerateNode,
    SqlValidateNode,
)
from sqlbot_platform.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
    WorkflowPolicies,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry


def _trace_metadata(label: str, output_path: tuple[str, ...], *, redaction: str = "none") -> dict:
    return {
        "display": {"label": label},
        "trace": {
            "output_path": list(output_path),
            "redaction": redaction,
        },
    }


def _capability_node(
    name: str,
    handler: str,
    label: str,
    output_path: tuple[str, ...],
    *,
    redaction: str = "none",
) -> NodeDefinition:
    return NodeDefinition(
        name=name,
        type=NodeType.CAPABILITY,
        handler=handler,
        metadata=_trace_metadata(label, output_path, redaction=redaction),
    )


def build_chatbi_minimal_definition() -> WorkflowDefinition:
    """构造最小 ChatBI 问数图。

    图中 SQL 校验和权限应用是执行 SQL 的前置节点，且默认失败分支会直接进入答案节点，
    从结构上保证权限拒绝或 SQL 校验失败时不会调用 `sql.execute`。
    """

    nodes = {
        "understand_question": _capability_node(
            "understand_question",
            "query.understand",
            "理解问题",
            ("variables", "understanding"),
        ),
        "retrieve_schema": _capability_node(
            "retrieve_schema",
            "schema.retrieve",
            "读取数据结构",
            ("variables", "schema"),
        ),
        "generate_sql": _capability_node(
            "generate_sql",
            "sql.generate",
            "生成查询",
            ("variables", "sql"),
            redaction="sql_summary",
        ),
        "validate_sql": _capability_node(
            "validate_sql",
            "sql.validate",
            "校验查询",
            ("variables", "sql_validation"),
        ),
        "apply_permission": _capability_node(
            "apply_permission",
            "permission.apply",
            "应用权限",
            ("variables", "permission"),
        ),
        "execute_sql": _capability_node(
            "execute_sql",
            "sql.execute",
            "查询数据",
            ("variables", "sql_result"),
        ),
        "generate_answer": _capability_node(
            "generate_answer",
            "answer.generate",
            "生成答案",
            ("variables", "answer"),
        ),
        "finish": NodeDefinition(
            name="finish",
            type=NodeType.TERMINAL,
            handler="answer.finish",
            metadata=_trace_metadata("结束", ("variables", "completed")),
        ),
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
