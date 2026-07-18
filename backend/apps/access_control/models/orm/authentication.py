"""认证配置 ORM。"""

from sqlalchemy import BigInteger, Text
from sqlmodel import Field, SQLModel

from common.core.models import SnowflakeBase


class AuthenticationBaseModel(SQLModel):
    name: str = Field(max_length=255, nullable=False)
    type: int = Field(nullable=False, default=0)
    config: str | None = Field(default=None, sa_type=Text, nullable=True)


class AuthenticationModel(SnowflakeBase, AuthenticationBaseModel, table=True):
    __tablename__ = "sys_authentication"

    create_time: int = Field(default=0, sa_type=BigInteger, nullable=False)
    enable: bool = Field(default=False, nullable=False)
    valid: bool = Field(default=False, nullable=False)
