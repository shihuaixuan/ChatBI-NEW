"""工作空间与成员管理 DTO。"""

from pydantic import BaseModel, Field

from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO

from .identity import UserEditor


class WorkspaceBase(BaseModel):
    name: str = Field(max_length=255)


class WorkspaceEditor(WorkspaceBase, BaseCreatorDTO):
    pass


class WorkspaceRecord(WorkspaceEditor):
    create_time: int


class UserWsBase(BaseModel):
    uid_list: list[int] = Field(description=f"{PLACEHOLDER_PREFIX}uid")
    oid: int | None = Field(default=None, description=f"{PLACEHOLDER_PREFIX}oid")


class UserWsDTO(UserWsBase):
    weight: int | None = Field(
        default=0,
        description=f"{PLACEHOLDER_PREFIX}weight",
    )


class UserWsEditor(BaseModel):
    uid: int = Field(description=f"{PLACEHOLDER_PREFIX}uid")
    oid: int = Field(description=f"{PLACEHOLDER_PREFIX}oid")
    weight: int = Field(default=0, description=f"{PLACEHOLDER_PREFIX}weight")


class WorkspaceUser(UserEditor):
    weight: int
    create_time: int


class UserWs(BaseCreatorDTO):
    name: str = Field(description="user_name")


class UserWsOption(UserWs):
    account: str = Field(description="user_account")

