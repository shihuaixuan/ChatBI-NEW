"""权限变量与用户变量绑定 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

AccessVariableValue: TypeAlias = str | int | float


class UserVariableAssignment(BaseModel):
    """用户绑定的自定义权限变量值。"""

    model_config = ConfigDict(populate_by_name=True)

    variable_id: int = Field(alias="variableId")
    variable_values: list[AccessVariableValue] = Field(alias="variableValues")

    @field_validator("variable_values", mode="before")
    @classmethod
    def reject_boolean_values(cls, values: object) -> object:
        if isinstance(values, list) and any(isinstance(value, bool) for value in values):
            raise ValueError("BOOLEAN_VARIABLE_VALUE_NOT_ALLOWED")
        return values


class AccessVariableInput(BaseModel):
    id: int | None = None
    name: str
    var_type: str
    value: list[AccessVariableValue]

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_values(cls, values: object) -> object:
        if isinstance(values, list) and any(isinstance(value, bool) for value in values):
            raise ValueError("BOOLEAN_VARIABLE_VALUE_NOT_ALLOWED")
        return values


class AccessVariableFilter(BaseModel):
    name: str | None = None


class AccessVariableRecord(BaseModel):
    id: int
    name: str
    var_type: str
    type: str
    value: list[AccessVariableValue]
    create_time: datetime | None = None
    create_by: int | None = None


class AccessVariableCreateData(BaseModel):
    name: str
    var_type: str
    type: str
    value: list[AccessVariableValue]
    create_time: datetime
    create_by: int


class AccessVariableUpdateData(BaseModel):
    name: str
    value: list[AccessVariableValue]
