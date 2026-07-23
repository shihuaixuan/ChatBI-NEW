"""行列权限配置持久化模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Integer, Text
from sqlmodel import Field, SQLModel


class DataPermissionModel(SQLModel, table=True):
    __tablename__ = "ds_permission"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
    )
    name: str | None = Field(default=None, max_length=128, nullable=True)
    enable: bool = Field(default=True, nullable=False)
    auth_target_type: str | None = Field(default=None, max_length=128)
    auth_target_id: int | None = Field(default=None, sa_type=BigInteger)
    type: str = Field(max_length=64, nullable=False)
    ds_id: int | None = Field(default=None, sa_type=BigInteger)
    table_id: int | None = Field(default=None, sa_type=BigInteger)
    expression_tree: str | None = Field(default=None, sa_type=Text)
    permissions: str | None = Field(default=None, sa_type=Text)
    white_list_user: str | None = Field(default=None, sa_type=Text)
    create_time: datetime | None = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=True),
    )


class DataRuleModel(SQLModel, table=True):
    __tablename__ = "ds_rules"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
    )
    oid: int | None = Field(default=None, sa_type=BigInteger)
    enable: bool = Field(default=True, nullable=False)
    name: str = Field(max_length=128, nullable=False)
    description: str | None = Field(default=None, max_length=512)
    permission_list: str | None = Field(default=None, sa_type=Text)
    user_list: str | None = Field(default=None, sa_type=Text)
    white_list_user: str | None = Field(default=None, sa_type=Text)
    create_time: datetime | None = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime, nullable=True),
    )


__all__ = ["DataPermissionModel", "DataRuleModel"]
