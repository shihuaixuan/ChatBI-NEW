"""Assistant 持久化模型。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import BigInteger, Field, SQLModel, Text

from common.core.models import SnowflakeBase


class AssistantBaseModel(SQLModel):
    name: str = Field(max_length=255, nullable=False)
    type: int = Field(nullable=False, default=0)
    domain: str = Field(max_length=255, nullable=False)
    description: str | None = Field(default=None, sa_type=Text, nullable=True)
    configuration: str | None = Field(default=None, sa_type=Text, nullable=True)
    create_time: int = Field(default=0, sa_type=BigInteger)
    app_id: str | None = Field(default=None, max_length=255, nullable=True)
    app_secret: str | None = Field(default=None, max_length=255, nullable=True)
    oid: int = Field(nullable=False, sa_type=BigInteger, default=1)
    enable_custom_model: bool | None = Field(default=False, nullable=True)
    custom_model: str | None = Field(default=None, max_length=255, nullable=True)


class AssistantModel(SnowflakeBase, AssistantBaseModel, table=True):
    __tablename__ = "sys_assistant"
    __table_args__ = (
        CheckConstraint(
            "type IN (0, 1, 2, 3, 4)",
            name="ck_sys_assistant_type",
        ),
        UniqueConstraint("app_id", name="uq_sys_assistant_app_id"),
        UniqueConstraint("app_secret", name="uq_sys_assistant_app_secret"),
        Index("ix_sys_assistant_oid_type", "oid", "type"),
    )
