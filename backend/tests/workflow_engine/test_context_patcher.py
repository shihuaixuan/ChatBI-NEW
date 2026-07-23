import pytest

from sqlbot_platform.workflow_engine.domain.artifact import ArtifactRef
from sqlbot_platform.workflow_engine.domain.context import (
    ContextPatch,
    ControlContext,
    WorkflowContext,
)
from sqlbot_platform.workflow_engine.runtime.context_patcher import (
    ContextPatcher,
    ContextPatchError,
)


def _context() -> WorkflowContext:
    return WorkflowContext(
        request={"tenant_id": 1, "user_id": 7, "question": "销售额"},
        conversation={"messages": [{"role": "user", "content": "销售额"}]},
        variables={"intent": "query", "nested": {"old": True}},
        control=ControlContext(current_node="understand"),
    )


def test_context_patcher_applies_set_append_remove_and_artifact_updates():
    artifact = ArtifactRef(
        artifact_id="sql-1",
        kind="sql",
        content_type="text/sql",
        size=16,
        digest="sha256:sql",
    )
    patch = ContextPatch(
        set_values={"variables.metric": "revenue", "variables.nested.new": 1},
        append_values={"conversation.messages": [{"role": "assistant", "content": "处理中"}]},
        remove_paths=["variables.intent", "variables.nested.old"],
        artifact_updates={"generated_sql": artifact},
    )

    updated = ContextPatcher().apply(_context(), patch)

    assert updated.variables == {"metric": "revenue", "nested": {"new": 1}}
    assert len(updated.conversation["messages"]) == 2
    assert updated.artifacts["generated_sql"].artifact_id == "sql-1"
    assert updated.control.current_node == "understand"


@pytest.mark.parametrize("path", ["request.user_id", "request.tenant_id", "control.current_node"])
def test_context_patcher_rejects_protected_namespaces(path):
    original = _context()

    with pytest.raises(ContextPatchError) as exc_info:
        ContextPatcher().apply(original, ContextPatch(set_values={path: "tampered"}))

    assert exc_info.value.code == "PATCH_PATH_FORBIDDEN"
    assert original.request["user_id"] == 7
    assert original.control.current_node == "understand"


def test_context_patcher_is_atomic_when_later_operation_fails():
    original = _context()
    patch = ContextPatch(
        set_values={"variables.metric": "revenue"},
        append_values={"variables.intent": ["invalid because current value is not a list"]},
    )

    with pytest.raises(ContextPatchError) as exc_info:
        ContextPatcher().apply(original, patch)

    assert exc_info.value.code == "PATCH_TARGET_NOT_LIST"
    assert "metric" not in original.variables
    assert original.variables["intent"] == "query"


def test_context_patcher_rejects_path_without_namespace():
    with pytest.raises(ContextPatchError) as exc_info:
        ContextPatcher().apply(_context(), ContextPatch(set_values={"metric": "revenue"}))

    assert exc_info.value.code == "INVALID_CONTEXT_PATH"
