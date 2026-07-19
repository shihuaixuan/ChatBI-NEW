from sqlalchemy import BigInteger, Column, Identity, Text
from sqlmodel import Field, SQLModel


class CoreTable(SQLModel, table=True):
    """数据源物理表缓存持久化对象。"""

    __tablename__ = "core_table"

    id: int = Field(
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        )
    )
    ds_id: int = Field(sa_column=Column(BigInteger()))
    checked: bool = Field(default=True)
    table_name: str = Field(sa_column=Column(Text))
    table_comment: str = Field(sa_column=Column(Text))
    custom_comment: str = Field(sa_column=Column(Text))
    # 旧列暂时保留用于数据库兼容，P3 不再向该列写入新的 Embedding。
    embedding: str = Field(sa_column=Column(Text, nullable=True))


class CoreField(SQLModel, table=True):
    """数据源物理字段缓存持久化对象。"""

    __tablename__ = "core_field"

    id: int = Field(
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        )
    )
    ds_id: int = Field(sa_column=Column(BigInteger()))
    table_id: int = Field(sa_column=Column(BigInteger()))
    checked: bool = Field(default=True)
    field_name: str = Field(sa_column=Column(Text))
    field_type: str = Field(max_length=128, nullable=True)
    field_comment: str = Field(sa_column=Column(Text))
    custom_comment: str = Field(sa_column=Column(Text))
    field_index: int = Field(sa_column=Column(BigInteger()))
