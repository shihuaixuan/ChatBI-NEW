"""权限变量持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AccessVariableModel(SQLModel, table=True):
    __tablename__ = "system_variable"
    __table_args__ = (
        CheckConstraint(
            "type IN ('system', 'custom')",
            name="ck_system_variable_type",
        ),
        CheckConstraint(
            "var_type IN ('text', 'number', 'datetime')",
            name="ck_system_variable_var_type",
        ),
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_system_variable_name_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(value) = 'array'",
            name="ck_system_variable_value_array",
        ),
        CheckConstraint(
            "type = 'system' OR create_by IS NOT NULL",
            name="ck_system_variable_custom_creator",
        ),
        UniqueConstraint("name", name="uq_system_variable_name"),
        Index("ix_system_variable_type_name", "type", "name"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        ),
    )
    name: str = Field(max_length=128, nullable=False)
    var_type: str = Field(max_length=128, nullable=False)
    type: str = Field(max_length=128, nullable=False)
    value: list[Any] = Field(sa_column=Column(JSONB, nullable=False))
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    create_by: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
