"""权限变量定义和用户绑定校验 Service。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, cast

from apps.access_control.errors import (
    AccessVariableDefinitionError,
    AccessVariableNameExistsError,
    AccessVariableNotFoundError,
    AccessVariableSystemMutationError,
    UserVariableAssignmentError,
)
from apps.access_control.models.dto import (
    AccessVariableCreateData,
    AccessVariableInput,
    AccessVariableRecord,
    AccessVariableUpdateData,
    AccessVariableValue,
    UserVariableAssignment,
)
from apps.access_control.repository import AccessVariableRepository
from common.core.schemas import PaginatedResponse

CUSTOM_VARIABLE_TYPE: Final = "custom"
SYSTEM_VARIABLE_TYPE: Final = "system"
SUPPORTED_VARIABLE_VALUE_TYPES: Final = frozenset({"text", "number", "datetime"})


class AccessVariableService:
    def __init__(self, repository: AccessVariableRepository) -> None:
        self._repository = repository

    def get(self, variable_id: int) -> AccessVariableRecord:
        variable = self._repository.get(variable_id)
        if variable is None:
            raise AccessVariableNotFoundError(variable_id)
        return variable

    def save(
        self,
        editor: AccessVariableInput,
        current_user_id: int,
    ) -> AccessVariableRecord:
        name = editor.name.strip()
        if not name:
            raise AccessVariableDefinitionError("NAME_REQUIRED")
        duplicate = self._repository.get_by_name(name, exclude_id=editor.id)
        if duplicate is not None:
            raise AccessVariableNameExistsError(name)

        normalized_values = self._normalize_definition_values(
            editor.var_type,
            editor.value,
        )
        if editor.id is None:
            return self._repository.create(
                AccessVariableCreateData(
                    name=name,
                    var_type=editor.var_type,
                    type=CUSTOM_VARIABLE_TYPE,
                    value=normalized_values,
                    create_time=datetime.now(),
                    create_by=current_user_id,
                )
            )

        current = self.get(editor.id)
        if current.type == SYSTEM_VARIABLE_TYPE:
            raise AccessVariableSystemMutationError(editor.id)
        if current.var_type != editor.var_type:
            raise AccessVariableDefinitionError("VALUE_TYPE_IMMUTABLE")
        updated = self._repository.update(
            editor.id,
            AccessVariableUpdateData(name=name, value=normalized_values),
        )
        if updated is None:
            raise AccessVariableNotFoundError(editor.id)
        return updated

    def delete(self, variable_ids: list[int]) -> list[int]:
        unique_ids = list(dict.fromkeys(variable_ids))
        variables = self._repository.list_by_ids(set(unique_ids))
        found_by_id = {variable.id: variable for variable in variables}
        missing_ids = [value for value in unique_ids if value not in found_by_id]
        if missing_ids:
            raise AccessVariableNotFoundError(missing_ids[0])
        system_ids = [
            variable.id
            for variable in variables
            if variable.type == SYSTEM_VARIABLE_TYPE
        ]
        if system_ids:
            raise AccessVariableSystemMutationError(min(system_ids))
        return self._repository.delete(unique_ids)

    def list_all(self, keyword: str | None = None) -> list[AccessVariableRecord]:
        normalized_keyword = keyword.strip() if keyword and keyword.strip() else None
        return self._repository.list_all(normalized_keyword)

    async def list_page(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[AccessVariableRecord]:
        normalized_keyword = keyword.strip() if keyword and keyword.strip() else None
        return await self._repository.list_page(
            page=page,
            size=size,
            keyword=normalized_keyword,
        )

    def normalize_assignments(
        self,
        assignments: list[UserVariableAssignment] | None,
    ) -> list[UserVariableAssignment]:
        if not assignments:
            return []
        assignment_by_id: dict[int, UserVariableAssignment] = {}
        for assignment in assignments:
            if assignment.variable_id in assignment_by_id:
                raise UserVariableAssignmentError(f"DUPLICATE:{assignment.variable_id}")
            assignment_by_id[assignment.variable_id] = assignment

        variables = self._repository.list_by_ids(set(assignment_by_id))
        variable_by_id = {variable.id: variable for variable in variables}
        missing_ids = sorted(set(assignment_by_id) - set(variable_by_id))
        if missing_ids:
            raise AccessVariableNotFoundError(missing_ids[0])
        return [
            self.normalize_assignment(variable_by_id[variable_id], assignment)
            for variable_id, assignment in assignment_by_id.items()
        ]

    def normalize_assignment(
        self,
        variable: AccessVariableRecord,
        assignment: UserVariableAssignment,
    ) -> UserVariableAssignment:
        if variable.type != CUSTOM_VARIABLE_TYPE:
            raise UserVariableAssignmentError(f"SYSTEM:{variable.id}")
        if assignment.variable_id != variable.id:
            raise UserVariableAssignmentError(f"ID_MISMATCH:{variable.id}")
        values = assignment.variable_values
        if variable.var_type == "text":
            if not variable.value or any(
                not isinstance(value, str) or not value for value in variable.value
            ):
                raise AccessVariableDefinitionError(f"TEXT_VALUES:{variable.id}")
            if not values or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise UserVariableAssignmentError(f"TEXT_REQUIRED:{variable.id}")
            allowed_values = set(variable.value)
            normalized = list(dict.fromkeys(values))
            if any(value not in allowed_values for value in normalized):
                raise UserVariableAssignmentError(f"TEXT_OUT_OF_RANGE:{variable.id}")
        elif variable.var_type == "number":
            if len(variable.value) != 2 or not all(
                self._is_number(value) for value in variable.value
            ):
                raise AccessVariableDefinitionError(f"NUMBER_RANGE:{variable.id}")
            if len(values) != 1 or not self._is_number(values[0]):
                raise UserVariableAssignmentError(f"NUMBER_REQUIRED:{variable.id}")
            minimum = cast(int | float, variable.value[0])
            maximum = cast(int | float, variable.value[1])
            value = cast(int | float, values[0])
            if value < minimum or value > maximum:
                raise UserVariableAssignmentError(f"NUMBER_OUT_OF_RANGE:{variable.id}")
            normalized = [value]
        elif variable.var_type == "datetime":
            if len(variable.value) != 2 or not all(
                isinstance(value, str) for value in variable.value
            ):
                raise AccessVariableDefinitionError(f"DATETIME_RANGE:{variable.id}")
            if len(values) != 1 or not isinstance(values[0], str):
                raise UserVariableAssignmentError(f"DATETIME_REQUIRED:{variable.id}")
            date_minimum, date_maximum = variable.value
            parsed_value = self._parse_date(values[0], f"ASSIGNMENT:{variable.id}")
            if parsed_value < self._parse_date(
                str(date_minimum), f"MIN:{variable.id}"
            ) or (
                parsed_value > self._parse_date(str(date_maximum), f"MAX:{variable.id}")
            ):
                raise UserVariableAssignmentError(
                    f"DATETIME_OUT_OF_RANGE:{variable.id}"
                )
            normalized = [values[0]]
        else:
            raise AccessVariableDefinitionError(
                f"VALUE_TYPE_UNSUPPORTED:{variable.var_type}"
            )
        return UserVariableAssignment(
            variableId=variable.id,
            variableValues=normalized,
        )

    def _normalize_definition_values(
        self,
        variable_type: str,
        values: list[AccessVariableValue],
    ) -> list[AccessVariableValue]:
        if variable_type not in SUPPORTED_VARIABLE_VALUE_TYPES:
            raise AccessVariableDefinitionError(
                f"VALUE_TYPE_UNSUPPORTED:{variable_type}"
            )
        if variable_type == "text":
            if not values or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise AccessVariableDefinitionError("TEXT_VALUES_REQUIRED")
            text_values = [cast(str, value).strip() for value in values]
            return list(dict.fromkeys(text_values))
        if variable_type == "number":
            if len(values) != 2 or not all(self._is_number(value) for value in values):
                raise AccessVariableDefinitionError("NUMBER_RANGE_REQUIRED")
            minimum = cast(int | float, values[0])
            maximum = cast(int | float, values[1])
            if minimum > maximum:
                raise AccessVariableDefinitionError("NUMBER_RANGE_INVALID")
            return [minimum, maximum]
        if len(values) != 2 or not all(isinstance(value, str) for value in values):
            raise AccessVariableDefinitionError("DATETIME_RANGE_REQUIRED")
        start = self._parse_date(str(values[0]), "RANGE_START")
        end = self._parse_date(str(values[1]), "RANGE_END")
        if start > end:
            raise AccessVariableDefinitionError("DATETIME_RANGE_INVALID")
        return values

    @staticmethod
    def _is_number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _parse_date(value: str, field: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AccessVariableDefinitionError(f"DATETIME_INVALID:{field}") from exc
