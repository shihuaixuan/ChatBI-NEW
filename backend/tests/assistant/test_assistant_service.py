"""Assistant 领域业务规则测试。"""

import json
from unittest.mock import Mock

import pytest

from apps.assistant.errors import (
    AssistantConfigurationError,
    AssistantCustomModelError,
    AssistantDatasourceScopeError,
    AssistantWorkspaceMismatchError,
)
from apps.assistant.models.dto import (
    AssistantBase,
    AssistantDTO,
    AssistantHeader,
    AssistantRecord,
    AssistantUiSchema,
)
from apps.assistant.services import AssistantService
from apps.datasource import DatasourceSummary


def _record(
    *,
    assistant_id: int = 10,
    workspace_id: int = 7,
    assistant_type: int = 0,
    configuration: str | None = None,
) -> AssistantRecord:
    return AssistantRecord(
        id=assistant_id,
        name="销售助手",
        domain="https://example.com",
        type=assistant_type,
        configuration=configuration,
        description=None,
        oid=workspace_id,
        enable_custom_model=False,
        custom_model=None,
        create_time=1,
    )


def _service(repository: Mock, datasource_catalog: Mock, *, model_exists=None):
    return AssistantService(
        repository,
        datasource_catalog,
        model_exists=model_exists or (lambda model_id: model_id == 99),
        generate_app_id=lambda: "app-id",
        generate_app_secret=lambda: "app-secret",
        external_datasource_factory=Mock(),
    )


def test_create_rejects_datasources_outside_current_workspace() -> None:
    repository = Mock()
    datasource_catalog = Mock()
    datasource_catalog.list_for_workspace.return_value = [
        DatasourceSummary(id=1, name="允许的数据源")
    ]
    service = _service(repository, datasource_catalog)
    creator = AssistantBase(
        name="销售助手",
        domain="https://example.com",
        type=0,
        configuration=json.dumps({"oid": 7, "public_list": [1, 2]}),
    )

    with pytest.raises(AssistantDatasourceScopeError):
        service.create(creator, current_workspace_id=7)

    repository.create.assert_not_called()


def test_create_normalizes_local_datasource_scope_once() -> None:
    repository = Mock()
    datasource_catalog = Mock()
    datasource_catalog.list_for_workspace.return_value = [
        DatasourceSummary(id=2, name="订单库"),
        DatasourceSummary(id=1, name="销售库"),
    ]

    def create(data):
        return AssistantRecord(id=10, **data.model_dump())

    repository.create.side_effect = create
    service = _service(repository, datasource_catalog)
    creator = AssistantBase(
        name="销售助手",
        domain="https://example.com",
        type=0,
        configuration=json.dumps(
            {"oid": "7", "public_list": [1, "1", 2], "private_list": []}
        ),
    )

    created = service.create(creator, current_workspace_id=7)

    normalized = json.loads(created.configuration or "{}")
    assert created.oid == 7
    assert normalized["oid"] == 7
    assert normalized["public_list"] == [1, 2]
    datasource_catalog.list_for_workspace.assert_called_once_with(7, [1, 2])


def test_enabled_custom_model_must_reference_existing_model() -> None:
    repository = Mock()
    datasource_catalog = Mock()
    service = _service(
        repository,
        datasource_catalog,
        model_exists=lambda model_id: False,
    )
    creator = AssistantBase(
        name="销售助手",
        domain="https://example.com",
        type=1,
        configuration=json.dumps({"endpoint": "/datasources"}),
        enable_custom_model=True,
        custom_model="99",
    )

    with pytest.raises(AssistantCustomModelError):
        service.create(creator, current_workspace_id=7)

    repository.create.assert_not_called()


def test_update_cannot_cross_workspace() -> None:
    repository = Mock()
    repository.get.return_value = _record(workspace_id=8)
    service = _service(repository, Mock())
    editor = AssistantDTO(
        id=10,
        name="销售助手",
        domain="https://example.com",
        type=0,
        configuration=json.dumps({"oid": 7, "public_list": []}),
    )

    with pytest.raises(AssistantWorkspaceMismatchError):
        service.update(editor, current_workspace_id=7)

    repository.update.assert_not_called()


def test_detail_cannot_cross_workspace() -> None:
    repository = Mock()
    repository.get.return_value = _record(workspace_id=8)
    service = _service(repository, Mock())

    with pytest.raises(AssistantWorkspaceMismatchError):
        service.get_for_workspace(10, current_workspace_id=7)


def test_offline_assistant_only_lists_public_datasources() -> None:
    datasource_catalog = Mock()
    datasource_catalog.list_for_workspace.return_value = [
        DatasourceSummary(id=2, name="订单库")
    ]
    service = _service(Mock(), datasource_catalog)
    assistant = AssistantHeader(
        id=10,
        name="销售助手",
        domain="https://example.com",
        type=0,
        configuration=json.dumps({"oid": 7, "public_list": [2]}),
        oid=7,
        online=False,
    )

    result = service.list_datasources(assistant)

    assert [item.id for item in result] == [2]
    datasource_catalog.list_for_workspace.assert_called_once_with(7, [2])


def test_datasource_list_rejects_inconsistent_workspace_configuration() -> None:
    service = _service(Mock(), Mock())
    assistant = AssistantHeader(
        id=10,
        name="销售助手",
        domain="https://example.com",
        type=0,
        configuration=json.dumps({"oid": 8, "public_list": [2]}),
        oid=7,
        online=False,
    )

    with pytest.raises(AssistantConfigurationError, match="WORKSPACE_MISMATCH"):
        service.list_datasources(assistant)


def test_ui_update_preserves_assets_that_are_not_submitted() -> None:
    repository = Mock()
    repository.get.return_value = _record(
        configuration=json.dumps({"logo": "old-logo", "theme": "dark"})
    )

    def update_configuration(_assistant_id, configuration):
        return _record(configuration=configuration)

    repository.update_configuration.side_effect = update_configuration
    service = _service(repository, Mock())

    result = service.update_ui(
        AssistantUiSchema(id=10, welcome="欢迎使用"),
        uploaded_asset_ids={},
        current_workspace_id=7,
    )

    updated = json.loads(result.assistant.configuration or "{}")
    assert updated["logo"] == "old-logo"
    assert updated["theme"] == "dark"
    assert updated["welcome"] == "欢迎使用"
    assert result.obsolete_asset_ids == []
