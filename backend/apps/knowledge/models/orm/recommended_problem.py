from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Text
from sqlmodel import Field, SQLModel


class RecommendedProblem(SQLModel, table=True):
    """推荐问题持久化对象，继续映射原数据库表。"""

    __tablename__ = "ds_recommended_problem"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        ),
    )
    datasource_id: int = Field(sa_column=Column(BigInteger()))
    question: str = Field(sa_column=Column(Text))
    remark: str = Field(sa_column=Column(Text))
    sort: int = Field(sa_column=Column(BigInteger()))
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=False), nullable=True),
    )
    create_by: int = Field(sa_column=Column(BigInteger()))
