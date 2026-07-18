"""用户与外部身份绑定 ORM。"""

from typing import Any

from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from common.core.models import SnowflakeBase
from common.core.security import default_md5_pwd
from common.utils.time import get_timestamp


class BaseUserPO(SQLModel):
    account: str = Field(max_length=255)
    oid: int = Field(nullable=False, sa_type=BigInteger, default=0)
    name: str = Field(max_length=255)
    password: str = Field(default_factory=default_md5_pwd, max_length=255)
    email: str = Field(max_length=255)
    status: int = Field(default=0, nullable=False)
    origin: int = Field(nullable=False, default=0)
    create_time: int = Field(
        default_factory=get_timestamp,
        sa_type=BigInteger,
        nullable=False,
    )
    language: str = Field(max_length=255, default="zh-CN")
    system_variables: list[Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )


class UserModel(SnowflakeBase, BaseUserPO, table=True):
    __tablename__ = "sys_user"
    __table_args__ = (UniqueConstraint("account", name="uq_sys_user_account"),)


class UserPlatformBase(SQLModel):
    uid: int = Field(nullable=False, sa_type=BigInteger)
    origin: int = Field(nullable=False, default=0)
    platform_uid: str = Field(max_length=255, nullable=False)


class UserPlatformModel(SnowflakeBase, UserPlatformBase, table=True):
    __tablename__ = "sys_user_platform"
