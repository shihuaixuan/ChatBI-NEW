from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class CoreDatasource(SQLModel, table=True):
    """数据源连接定义持久化对象。"""

    __tablename__ = "core_datasource"

    id: int = Field(
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        )
    )
    name: str = Field(max_length=128, nullable=False)
    description: str = Field(max_length=512, nullable=True)
    type: str = Field(max_length=64)
    type_name: str = Field(max_length=64, nullable=True)
    configuration: str = Field(sa_column=Column(Text))
    create_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    create_by: int = Field(sa_column=Column(BigInteger()))
    status: str = Field(max_length=64, nullable=True)
    num: str = Field(max_length=256, nullable=True)
    oid: int = Field(sa_column=Column(BigInteger()))
    table_relation: list = Field(sa_column=Column(JSONB, nullable=True))
    # 旧列暂时保留用于数据库兼容，P3 不再向该列写入新的 Embedding。
    embedding: str = Field(sa_column=Column(Text, nullable=True))
    recommended_config: int = Field(sa_column=Column(BigInteger()))
