"""API Key ORM。"""

from sqlalchemy import BigInteger, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from common.core.models import SnowflakeBase


class ApiKeyBaseModel(SQLModel):
    access_key: str = Field(max_length=255, nullable=False)
    secret_key: str = Field(max_length=255, nullable=False)
    create_time: int = Field(default=0, sa_type=BigInteger, nullable=False)
    uid: int = Field(default=0, nullable=False, sa_type=BigInteger)
    status: bool = Field(default=True, nullable=False)


class ApiKeyModel(SnowflakeBase, ApiKeyBaseModel, table=True):
    __tablename__ = "sys_apikey"
    __table_args__ = (
        UniqueConstraint("access_key", name="uq_sys_apikey_access_key"),
        Index("ix_sys_apikey_uid", "uid"),
    )
