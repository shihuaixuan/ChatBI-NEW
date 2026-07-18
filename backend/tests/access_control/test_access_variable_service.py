"""权限变量定义和用户绑定规则测试。"""

from datetime import datetime
from unittest.mock import Mock

import pytest

from apps.access_control.errors import (
    AccessVariableDefinitionError,
    AccessVariableSystemMutationError,
    UserVariableAssignmentError,
)
from apps.access_control.models.dto import (
    AccessVariableInput,
    AccessVariableRecord,
    UserVariableAssignment,
)
from apps.access_control.services import AccessVariableService


def _variable(
    *,
    variable_id: int = 10,
    variable_type: str = "text",
    scope: str = "custom",
    values: list | None = None,
) -> AccessVariableRecord:
    return AccessVariableRecord(
        id=variable_id,
        name="门店范围",
        var_type=variable_type,
        type=scope,
        value=values or ["华东", "华南"],
        create_time=datetime.now(),
        create_by=1 if scope == "custom" else None,
    )


def test_create_normalizes_duplicate_text_values() -> None:
    repository = Mock()
    repository.get_by_name.return_value = None
    repository.create.side_effect = lambda data: AccessVariableRecord(
        id=10,
        **data.model_dump(),
    )
    service = AccessVariableService(repository)

    created = service.save(
        AccessVariableInput(
            name=" 门店范围 ",
            var_type="text",
            value=["华东", "华东", "华南"],
        ),
        current_user_id=1,
    )

    assert created.name == "门店范围"
    assert created.value == ["华东", "华南"]


def test_number_definition_rejects_reversed_range() -> None:
    repository = Mock()
    repository.get_by_name.return_value = None
    service = AccessVariableService(repository)

    with pytest.raises(AccessVariableDefinitionError, match="NUMBER_RANGE_INVALID"):
        service.save(
            AccessVariableInput(name="金额", var_type="number", value=[100, 1]),
            current_user_id=1,
        )


def test_system_variable_cannot_be_deleted() -> None:
    repository = Mock()
    repository.list_by_ids.return_value = [_variable(scope="system")]
    service = AccessVariableService(repository)

    with pytest.raises(AccessVariableSystemMutationError):
        service.delete([10])

    repository.delete.assert_not_called()


def test_user_text_assignment_must_be_within_definition() -> None:
    service = AccessVariableService(Mock())

    with pytest.raises(UserVariableAssignmentError, match="TEXT_OUT_OF_RANGE"):
        service.normalize_assignment(
            _variable(),
            UserVariableAssignment(
                variableId=10,
                variableValues=["未授权区域"],
            ),
        )
