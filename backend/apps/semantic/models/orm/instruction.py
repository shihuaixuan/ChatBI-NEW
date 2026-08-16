"""数据集级模块化 instructions 资产。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    Text,
    text,
)
from sqlmodel import Field, SQLModel


class SemanticDatasetInstruction(SQLModel, table=True):
    """按数据集、模块和版本管理可灰度启用的固定指令。"""

    __tablename__ = "headless_dataset_instruction"
    __table_args__ = (
        CheckConstraint(
            "module IN ('sql_generation', 'question_categorization')",
            name="ck_headless_dataset_instruction_module",
        ),
        Index(
            "ux_headless_dataset_instruction_version",
            "oid",
            "dataset_id",
            "module",
            "version",
            unique=True,
        ),
        Index(
            "idx_headless_dataset_instruction_enabled",
            "oid",
            "dataset_id",
            "module",
            "enabled",
        ),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    dataset_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    module: str = Field(max_length=64, nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))
    version: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default=text("1")))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))
    updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False), nullable=True))


__all__ = ["SemanticDatasetInstruction"]
