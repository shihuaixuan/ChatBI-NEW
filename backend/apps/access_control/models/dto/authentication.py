"""认证与 API Key DTO。"""

from pydantic import BaseModel, Field

from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO


class AuthenticationRecord(BaseModel):
    id: int
    name: str
    type: int
    config: str | None
    create_time: int
    enable: bool
    valid: bool


class ApiKeyStatus(BaseModel):
    id: int = Field(description=f"{PLACEHOLDER_PREFIX}id")
    status: bool = Field(description=f"{PLACEHOLDER_PREFIX}status")


class ApiKeyGridItem(BaseCreatorDTO):
    access_key: str = Field(description="Access Key")
    secret_key: str = Field(description="Secret Key")
    status: bool = Field(description=f"{PLACEHOLDER_PREFIX}status")
    create_time: int = Field(description=f"{PLACEHOLDER_PREFIX}create_time")


class ApiKeyRecord(ApiKeyGridItem):
    uid: int


class LogoutDTO(BaseModel):
    token: str | None = None
    flag: str | None = "default"
    origin: int | None = 0
    data: str | None = None


# 保留前端和 XPack 已使用的类名。
ApikeyStatus = ApiKeyStatus
ApikeyGridItem = ApiKeyGridItem
LogoutSchema = LogoutDTO
