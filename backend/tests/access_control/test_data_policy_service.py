"""结构化行列数据策略测试。"""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.access_control import data_policy as data_policy_module
from apps.access_control.data_policy import SessionDataPolicyProvider
from apps.access_control.errors import (
    DataPolicyConfigurationError,
    UserVariableAssignmentError,
)
from apps.access_control.models.dto import (
    AccessVariableRecord,
    DataPolicy,
    DataPolicySubject,
    StoredDataPermission,
    StoredDataRule,
)
from apps.access_control.models.orm import DataPermissionModel, DataRuleModel
from apps.access_control.services import AccessVariableService, DataPolicyService
from apps.api import api_router
from apps.datasource import (
    DatasourcePolicyField,
    DatasourcePolicySchema,
    DatasourcePolicyTable,
)


def test_data_permission_management_routes_are_local_access_control_routes():
    routes = [
        route
        for route in api_router.routes
        if route.path.startswith("/ds_permission")
    ]

    assert {route.path for route in routes} == {
        "/ds_permission/list",
        "/ds_permission/get/{id}",
        "/ds_permission/save",
        "/ds_permission/delete/{id}",
    }
    assert all(
        route.endpoint.__module__ == "apps.access_control.api.data_permission"
        for route in routes
    )
    assert DataPermissionModel.__tablename__ == "ds_permission"
    assert DataRuleModel.__tablename__ == "ds_rules"


def _schema() -> DatasourcePolicySchema:
    return DatasourcePolicySchema(
        id=7,
        workspace_id=3,
        database_type="mysql",
        identifier_prefix="`",
        identifier_suffix="`",
        tables=[
            DatasourcePolicyTable(
                id=11,
                name="orders",
                fields=[
                    DatasourcePolicyField(id=101, name="owner", data_type="varchar"),
                    DatasourcePolicyField(
                        id=102,
                        name="secret_cost",
                        data_type="decimal",
                    ),
                ],
            )
        ],
    )


def _subject(*, assignments=None) -> DataPolicySubject:
    return DataPolicySubject(
        user_id=9,
        workspace_id=3,
        name="O'Reilly",
        account="member",
        email="member@example.com",
        variable_assignments=assignments or [],
    )


def _rule() -> StoredDataRule:
    return StoredDataRule(
        id=1,
        workspace_id=3,
        permission_list="[21, 22]",
        user_list='["9"]',
        white_list_user="[]",
    )


def _service(permissions, variable=None) -> DataPolicyService:
    repository = Mock()
    repository.list_rules.return_value = [_rule()]
    repository.list_permissions.return_value = permissions
    datasource_catalog = Mock()
    datasource_catalog.get_policy_schema.return_value = _schema()
    variable_repository = Mock()
    variable_repository.get.return_value = variable
    return DataPolicyService(
        repository,
        datasource_catalog,
        AccessVariableService(variable_repository),
    )


def test_system_variable_resolves_to_escaped_structured_row_filter() -> None:
    row_permission = StoredDataPermission(
        id=21,
        permission_type="row",
        datasource_id=7,
        table_id=11,
        expression_tree=json.dumps(
            {
                "logic": "and",
                "items": [
                    {
                        "type": "item",
                        "field_id": 101,
                        "filter_type": "logic",
                        "term": "eq",
                        "value_type": "variable",
                        "variable_id": 1,
                    }
                ],
            }
        ),
    )
    system_variable = AccessVariableRecord(
        id=1,
        name="i18n_variable.name",
        var_type="text",
        type="system",
        value=["name"],
    )

    policy = _service([row_permission], system_variable).resolve(_subject(), 7)

    assert policy.row_filters[0].condition == "((`owner` = 'O''Reilly'))"
    predicate = policy.row_filters[0].expressions[0].items[0]
    assert predicate.values == ["O'Reilly"]


def test_column_permission_returns_stable_denied_column() -> None:
    column_permission = StoredDataPermission(
        id=22,
        permission_type="column",
        datasource_id=7,
        table_id=11,
        permissions=json.dumps([{"field_id": 102, "enable": False}]),
    )

    policy = _service([column_permission]).resolve(_subject(), 7)

    assert policy.denied_columns[0].model_dump() == {
        "table_id": 11,
        "table": "orders",
        "field_id": 102,
        "column": "secret_cost",
    }


def test_missing_custom_variable_assignment_never_removes_row_restriction() -> None:
    row_permission = StoredDataPermission(
        id=21,
        permission_type="row",
        datasource_id=7,
        table_id=11,
        expression_tree=json.dumps(
            {
                "logic": "and",
                "items": [
                    {
                        "type": "item",
                        "field_id": 101,
                        "filter_type": "logic",
                        "term": "in",
                        "value_type": "variable",
                        "variable_id": 10,
                    }
                ],
            }
        ),
    )
    custom_variable = AccessVariableRecord(
        id=10,
        name="区域",
        var_type="text",
        type="custom",
        value=["华东", "华南"],
        create_by=1,
    )

    with pytest.raises(UserVariableAssignmentError, match="MISSING:10"):
        _service([row_permission], custom_variable).resolve(_subject(), 7)


def test_system_admin_has_no_row_or_column_restrictions() -> None:
    service = _service([])
    subject = _subject().model_copy(update={"is_system_admin": True})

    policy = service.resolve(subject, 7)

    assert policy.allowed is True
    assert policy.row_filters == []
    assert policy.denied_columns == []
    service._datasource_catalog.get_policy_schema.assert_not_called()


def test_requested_unknown_table_never_returns_empty_policy() -> None:
    with pytest.raises(DataPolicyConfigurationError, match="TABLE_NOT_FOUND:missing"):
        _service([]).resolve(_subject(), 7, table_names=["missing"])


def test_invalid_column_enable_never_expands_access() -> None:
    column_permission = StoredDataPermission(
        id=22,
        permission_type="column",
        datasource_id=7,
        table_id=11,
        permissions=json.dumps([{"field_id": 102, "enable": "false"}]),
    )

    with pytest.raises(DataPolicyConfigurationError, match="COLUMN_ENABLE"):
        _service([column_permission]).resolve(_subject(), 7)


def test_provider_denies_request_when_identity_is_incomplete() -> None:
    provider = SessionDataPolicyProvider(lambda: pytest.fail("不应创建数据库会话"))

    policy = provider.get_policy({})

    assert policy["allowed"] is False
    assert policy["error_code"] == "data_policy_denied"


def test_provider_returns_resolved_policy_for_valid_identity(monkeypatch) -> None:
    user = SimpleNamespace(id=9, oid=3, isAdmin=False)
    identity_service = SimpleNamespace(get_user_info=lambda user_id: user)
    monkeypatch.setattr(
        data_policy_module,
        "build_identity_workspace_service",
        lambda session: identity_service,
    )
    monkeypatch.setattr(
        data_policy_module,
        "resolve_data_policy",
        lambda session, current_user, datasource_id: DataPolicy(),
    )
    provider = SessionDataPolicyProvider(lambda: nullcontext(object()))

    policy = provider.get_policy(
        {"user_id": 9, "tenant_id": 3, "datasource_id": 7}
    )

    assert policy["allowed"] is True
    assert policy["reason"] == "permission_applied"
