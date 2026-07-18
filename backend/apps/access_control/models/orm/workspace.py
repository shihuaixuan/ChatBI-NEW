"""工作空间与成员关系 ORM。"""

from sqlalchemy import UniqueConstraint
from sqlmodel import BigInteger, Field, SQLModel

from common.core.models import SnowflakeBase


class WorkspaceBaseModel(SQLModel):
    name: str = Field(max_length=255, nullable=False)


class WorkspaceModel(SnowflakeBase, WorkspaceBaseModel, table=True):
    __tablename__ = "sys_workspace"

    create_time: int = Field(default=0, sa_type=BigInteger)


class UserWsBaseModel(SQLModel):
    uid: int = Field(nullable=False, sa_type=BigInteger)
    oid: int = Field(nullable=False, sa_type=BigInteger)
    weight: int = Field(default=0, nullable=False)


class UserWsModel(SnowflakeBase, UserWsBaseModel, table=True):
    __tablename__ = "sys_user_ws"
    __table_args__ = (
        UniqueConstraint("uid", "oid", name="uq_sys_user_ws_uid_oid"),
    )
