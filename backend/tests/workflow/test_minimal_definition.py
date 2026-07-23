from apps.chatbi.orchestration.graph.capabilities.gateway import ChatBICapabilityGateway
from apps.chatbi.orchestration.graph.conditions.core import register_chatbi_conditions
from apps.chatbi.orchestration.graph.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
    register_chatbi_minimal_handlers,
)
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.registry.workflow_registry import WorkflowRegistry


class FakeGateway(ChatBICapabilityGateway):
    def invoke(self, capability: str, request: dict, idempotency_key: str) -> dict:
        return {"capability": capability, "request": request, "idempotency_key": idempotency_key}


def test_minimal_definition_publishes_with_explicit_safety_order():
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    register_chatbi_minimal_handlers(handlers, FakeGateway())
    register_chatbi_conditions(conditions)

    definition = build_chatbi_minimal_definition()
    published = WorkflowRegistry(DefinitionValidator(handlers, conditions)).publish(definition)

    node_order = list(definition.nodes)
    assert published.name == "chatbi"
    assert published.version == "minimal-v1"
    assert node_order.index("validate_sql") < node_order.index("execute_sql")
    assert node_order.index("apply_permission") < node_order.index("execute_sql")
    assert {edge.condition for edge in definition.edges if edge.source == "validate_sql"} == {
        "sql.valid",
        None,
    }
    assert {edge.condition for edge in definition.edges if edge.source == "apply_permission"} == {
        "permission.allowed",
        None,
    }
