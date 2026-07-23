import pytest

from sqlbot_platform.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
)
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidationError,
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry


def _registries() -> tuple[HandlerRegistry, ConditionRegistry]:
    handlers = HandlerRegistry()
    conditions = ConditionRegistry()
    for name in ("start", "decision", "finish"):
        handlers.register(name, object())
    conditions.register("is_ready", object())
    return handlers, conditions


def _valid_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="chatbi",
        version="v1",
        start_node="start",
        nodes={
            "start": NodeDefinition(name="start", type=NodeType.TRANSFORM, handler="start"),
            "decision": NodeDefinition(name="decision", type=NodeType.DECISION, handler="decision"),
            "finish": NodeDefinition(name="finish", type=NodeType.TERMINAL, handler="finish"),
        },
        edges=[
            EdgeDefinition(source="start", target="decision"),
            EdgeDefinition(source="decision", target="finish", condition="is_ready", priority=10),
            EdgeDefinition(source="decision", target="finish", priority=100),
        ],
        input_schema={},
        output_schema={},
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda graph: setattr(graph, "start_node", "missing"), "START_NODE_NOT_FOUND"),
        (
            lambda graph: graph.edges.append(EdgeDefinition(source="missing", target="finish")),
            "EDGE_NODE_NOT_FOUND",
        ),
        (
            lambda graph: graph.edges.append(EdgeDefinition(source="start", target="finish")),
            "DUPLICATE_DEFAULT_EDGE",
        ),
        (lambda graph: graph.nodes.update({"orphan": NodeDefinition(name="orphan", type=NodeType.TERMINAL, handler="finish")}), "UNREACHABLE_NODE"),
        (lambda graph: graph.edges.clear(), "NODE_WITHOUT_OUTGOING_EDGE"),
        (lambda graph: setattr(graph.nodes["start"], "handler", "missing"), "HANDLER_NOT_FOUND"),
        (lambda graph: setattr(graph.edges[1], "condition", "missing"), "CONDITION_NOT_FOUND"),
    ],
)
def test_validator_rejects_invalid_graph(mutate, code):
    handlers, conditions = _registries()
    graph = _valid_definition()
    mutate(graph)

    with pytest.raises(DefinitionValidationError) as exc_info:
        DefinitionValidator(handlers, conditions).validate(graph)

    assert exc_info.value.code == code


def test_validator_accepts_bounded_cycle():
    handlers, conditions = _registries()
    graph = _valid_definition()
    graph.edges.insert(1, EdgeDefinition(source="decision", target="start", condition="is_ready", priority=1))

    DefinitionValidator(handlers, conditions).validate(graph)
