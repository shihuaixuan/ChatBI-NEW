"""用户身份与管理 DTO。"""

import re

from pydantic import BaseModel, Field, field_validator

from apps.access_control.models.dto.access_variable import UserVariableAssignment
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9]+([._-][a-zA-Z0-9]+)*@"
    r"([a-zA-Z0-9]+(-[a-zA-Z0-9]+)*\.)+"
    r"[a-zA-Z]{2,}$"
)
PWD_REGEX = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)"
    r"(?=.*[~!@#$%^&*()_+\-={}|:\"<>?`\[\];',./])"
    r"[A-Za-z\d~!@#$%^&*()_+\-={}|:\"<>?`\[\];',./]{8,20}$"
)


class UserStatus(BaseCreatorDTO):
    status: int = Field(default=1, description=f"{PLACEHOLDER_PREFIX}status")


class UserLanguage(BaseModel):
    language: str = Field(description=f"{PLACEHOLDER_PREFIX}language")


class BaseUser(BaseModel):
    account: str = Field(min_length=1, max_length=100, description="用户账号")
    oid: int


class BaseUserDTO(BaseUser, BaseCreatorDTO):
    language: str = Field(
        pattern=r"^(zh-CN|zh-TW|en|ko-KR)$",
        default="zh-CN",
        description="用户语言",
    )
    password: str
    status: int = 1
    origin: int = 0
    name: str

    def to_dict(self) -> dict[str, int | str]:
        return {"id": self.id, "account": self.account, "oid": self.oid}

    @field_validator("language")
    @classmethod
    def validate_language(cls, language: str) -> str:
        if not re.fullmatch(r"^(zh-CN|zh-TW|en|ko-KR)$", language):
            raise ValueError("Language must be 'zh-CN', 'zh-TW', 'en', or 'ko-KR'")
        return language


class UserCreator(BaseUser):
    name: str = Field(
        min_length=1,
        max_length=100,
        description=f"{PLACEHOLDER_PREFIX}user_name",
    )
    email: str = Field(
        min_length=1,
        max_length=100,
        description=f"{PLACEHOLDER_PREFIX}user_email",
    )
    status: int = Field(default=1, description=f"{PLACEHOLDER_PREFIX}status")
    origin: int | None = Field(default=0, description=f"{PLACEHOLDER_PREFIX}origin")
    oid_list: list[int] | None = Field(
        default=None,
        description=f"{PLACEHOLDER_PREFIX}oid",
    )
    system_variables: list[UserVariableAssignment] | None = Field(default_factory=list)


class UserEditor(UserCreator, BaseCreatorDTO):
    pass


class UserGrid(UserEditor):
    create_time: int = Field(description=f"{PLACEHOLDER_PREFIX}create_time")
    language: str = Field(
        default="zh-CN",
        description=f"{PLACEHOLDER_PREFIX}language",
    )


class UserRecord(BaseModel):
    """Repository 与 Service 之间使用的完整用户快照。"""

    id: int
    account: str
    oid: int
    name: str
    password: str
    email: str
    status: int
    origin: int
    create_time: int
    language: str
    system_variables: list[UserVariableAssignment] | None = None


class PwdEditor(BaseModel):
    pwd: str = Field(description=f"{PLACEHOLDER_PREFIX}origin_pwd")
    new_pwd: str = Field(description=f"{PLACEHOLDER_PREFIX}new_pwd")


class UserInfoDTO(UserEditor):
    language: str = "zh-CN"
    weight: int = 0
    isAdmin: bool = False
