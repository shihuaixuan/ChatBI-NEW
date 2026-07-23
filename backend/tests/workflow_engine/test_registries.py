import pytest

from sqlbot_platform.workflow_engine.api import extension as api_extension
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry
from sqlbot_platform.workflow_engine.registry.workflow_registry import (
    DefinitionNotFoundError,
    DuplicateDefinitionError,
    WorkflowRegistry,
)
from tests.workflow_engine.test_definition_validator import (
    _registries,
    _valid_definition,
)


def test_workflow_api_extension_requires_explicit_registration(monkeypatch):
    """未注册业务扩展时必须明确失败，不能回退到隐式业务实现。"""

    monkeypatch.setattr(api_extension, "_extension_factory", None)

    with pytest.raises(
        RuntimeError,
        match="WORKFLOW_API_EXTENSION_NOT_REGISTERED",
    ):
        api_extension.build_workflow_api_extension(object())  # type: ignore[arg-type]


def test_handler_and_condition_registry_reject_duplicate_names():
    handlers = HandlerRegistry()
    handlers.register("start", object())
    with pytest.raises(ValueError, match="HANDLER_ALREADY_REGISTERED"):
        handlers.register("start", object())

    conditions = ConditionRegistry()
    conditions.register("ready", object())
    with pytest.raises(ValueError, match="CONDITION_ALREADY_REGISTERED"):
        conditions.register("ready", object())


def test_workflow_registry_publishes_immutable_version_and_digest():
    handlers, conditions = _registries()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    source = _valid_definition()

    published = registry.publish(source)
    source.metadata["changed_after_publish"] = True
    loaded = registry.get("chatbi", "v1")
    loaded.metadata["changed_by_consumer"] = True
    reloaded = registry.get("chatbi", "v1")

    assert published.digest.startswith("sha256:")
    assert reloaded.metadata == {}


def test_workflow_registry_rejects_overwrite_and_allows_new_version():
    handlers, conditions = _registries()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))
    graph = _valid_definition()
    registry.publish(graph)

    with pytest.raises(DuplicateDefinitionError):
        registry.publish(graph)

    graph_v2 = graph.model_copy(update={"version": "v2"}, deep=True)
    registry.publish(graph_v2)

    assert registry.get("chatbi", "v2").version == "v2"


def test_workflow_registry_reports_stable_not_found_error():
    handlers, conditions = _registries()
    registry = WorkflowRegistry(DefinitionValidator(handlers, conditions))

    with pytest.raises(DefinitionNotFoundError, match="DEFINITION_NOT_FOUND"):
        registry.get("missing", "v1")
