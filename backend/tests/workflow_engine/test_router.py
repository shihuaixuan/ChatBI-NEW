import pytest

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.definition import (
    EdgeDefinition,
    NodeDefinition,
    NodeType,
    WorkflowDefinition,
)
from apps.workflow_engine.domain.execution import NodeExecutionResult, NodeResultStatus
from apps.workflow_engine.registry.condition_registry import ConditionRegistry
from apps.workflow_engine.runtime.router import (
    ConditionDecision,
    ConditionRouter,
    RoutingError,
)


class StaticCondition:
    def __init__(self, matched: bool, reason: str) -> None:
        self.matched = matched
        self.reason = reason

    def evaluate(self, context, result):
        return ConditionDecision(
            matched=self.matched,
            reason_code=self.reason,
            reason_summary=self.reason,
        )


class BrokenCondition:
    def evaluate(self, context, result):
        raise RuntimeError("private failure")


def _definition(edges: list[EdgeDefinition]) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="router",
        version="v1",
        start_node="source",
        nodes={
            "source": NodeDefinition(name="source", type=NodeType.DECISION, handler="source"),
            "high": NodeDefinition(name="high", type=NodeType.TERMINAL, handler="finish"),
            "low": NodeDefinition(name="low", type=NodeType.TERMINAL, handler="finish"),
            "fallback": NodeDefinition(name="fallback", type=NodeType.TERMINAL, handler="finish"),
        },
        edges=edges,
        input_schema={},
        output_schema={},
    )


def _result() -> NodeExecutionResult:
    return NodeExecutionResult(status=NodeResultStatus.SUCCEEDED)


def test_router_selects_first_matching_condition_by_priority():
    conditions = ConditionRegistry()
    conditions.register("high", StaticCondition(True, "HIGH_MATCH"))
    conditions.register("low", StaticCondition(True, "LOW_MATCH"))
    graph = _definition(
        [
            EdgeDefinition(source="source", target="low", condition="low", priority=20),
            EdgeDefinition(source="source", target="high", condition="high", priority=10),
            EdgeDefinition(source="source", target="fallback"),
        ]
    )

    decision = ConditionRouter(conditions).select(
        graph,
        graph.nodes["source"],
        WorkflowContext(),
        _result(),
    )

    assert decision.target == "high"
    assert decision.condition == "high"
    assert decision.reason_code == "HIGH_MATCH"


def test_router_uses_default_edge_after_false_and_broken_conditions():
    conditions = ConditionRegistry()
    conditions.register("false", StaticCondition(False, "NOT_READY"))
    conditions.register("broken", BrokenCondition())
    graph = _definition(
        [
            EdgeDefinition(source="source", target="high", condition="broken", priority=1),
            EdgeDefinition(source="source", target="low", condition="false", priority=2),
            EdgeDefinition(source="source", target="fallback", priority=0),
        ]
    )

    decision = ConditionRouter(conditions).select(
        graph,
        graph.nodes["source"],
        WorkflowContext(),
        _result(),
    )

    assert decision.target == "fallback"
    assert decision.reason_code == "DEFAULT_EDGE"
    assert decision.condition_errors == ["broken"]


def test_router_raises_stable_error_when_no_edge_matches():
    conditions = ConditionRegistry()
    conditions.register("false", StaticCondition(False, "NOT_READY"))
    graph = _definition(
        [EdgeDefinition(source="source", target="low", condition="false")]
    )

    with pytest.raises(RoutingError) as exc_info:
        ConditionRouter(conditions).select(
            graph,
            graph.nodes["source"],
            WorkflowContext(),
            _result(),
        )

    assert exc_info.value.code == "ROUTE_NOT_FOUND"
