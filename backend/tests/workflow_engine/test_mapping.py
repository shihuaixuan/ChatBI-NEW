import pytest

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.runtime.mapping import MappingError, MappingResolver


def _context() -> WorkflowContext:
    return WorkflowContext(
        request={"question": "销售额", "tenant_id": 1},
        conversation={"summary": "用户关注本月"},
        variables={"understanding": {"metric": "revenue"}},
    )


def test_mapping_resolver_builds_node_inputs_from_context_paths():
    inputs = MappingResolver().resolve_inputs(
        _context(),
        {
            "question": "request.question",
            "metric": "variables.understanding.metric",
            "summary": "conversation.summary",
        },
    )

    assert inputs == {"question": "销售额", "metric": "revenue", "summary": "用户关注本月"}


def test_mapping_resolver_converts_handler_outputs_to_patch():
    patch = MappingResolver().resolve_outputs(
        {"intent": "query", "confidence": 0.9},
        {
            "intent": "variables.understanding.intent",
            "confidence": "variables.understanding.confidence",
        },
    )

    assert patch.set_values["variables.understanding.intent"] == "query"
    assert patch.set_values["variables.understanding.confidence"] == 0.9


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (lambda resolver: resolver.resolve_inputs(_context(), {"missing": "variables.none"}), "MAPPING_PATH_NOT_FOUND"),
        (lambda resolver: resolver.resolve_outputs({}, {"missing": "variables.value"}), "MAPPING_OUTPUT_NOT_FOUND"),
        (lambda resolver: resolver.resolve_outputs({"value": 1}, {"value": "request.user_id"}), "MAPPING_TARGET_FORBIDDEN"),
    ],
)
def test_mapping_resolver_reports_stable_errors(operation, code):
    with pytest.raises(MappingError) as exc_info:
        operation(MappingResolver())

    assert exc_info.value.code == code
