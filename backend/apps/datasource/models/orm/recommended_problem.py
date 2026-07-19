from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Identity, Text
from sqlmodel import Field, SQLModel


class DsRecommendedProblem(SQLModel, table=True):
    """推荐问题旧表的持久化对象，等待 P4 迁入 Knowledge。"""

    __tablename__ = "ds_recommended_problem"

    id: int = Field(
        sa_column=Column(
            BigInteger,
            Identity(always=True),
            nullable=False,
            primary_key=True,
        )
    )
    datasource_id: int = Field(sa_column=Column(BigInteger()))
    question: str = Field(sa_column=Column(Text))
    remark: str = Field(sa_column=Column(Text))
    sort: int = Field(sa_column=Column(BigInteger()))
    create_time: datetime = Field(
        sa_column=Column(DateTime(timezone=False), nullable=True)
    )
    create_by: int = Field(sa_column=Column(BigInteger()))
