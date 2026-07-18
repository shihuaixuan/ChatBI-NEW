from typing import Optional

from pydantic import BaseModel, Field

from apps.access_control.models.dto import (
    EMAIL_REGEX as EMAIL_REGEX,
    PWD_REGEX as PWD_REGEX,
    BaseUser as BaseUser,
    BaseUserDTO as BaseUserDTO,
    PwdEditor as PwdEditor,
    UserCreator as UserCreator,
    UserEditor as UserEditor,
    UserGrid as UserGrid,
    UserInfoDTO as UserInfoDTO,
    UserLanguage as UserLanguage,
    UserStatus as UserStatus,
    UserWs as UserWs,
    UserWsBase as UserWsBase,
    UserWsDTO as UserWsDTO,
    UserWsEditor as UserWsEditor,
    UserWsOption as UserWsOption,
    WorkspaceUser as WorkspaceUser,
)
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO


class AssistantBase(BaseModel):
    name: str = Field(description=f"{PLACEHOLDER_PREFIX}model_name")
    domain: str = Field(description=f"{PLACEHOLDER_PREFIX}assistant_domain")
    type: int = Field(default=0, description=f"{PLACEHOLDER_PREFIX}assistant_type")  # 0普通小助手 1高级 4页面嵌入
    configuration: Optional[str] = Field(default=None, description=f"{PLACEHOLDER_PREFIX}assistant_configuration")
    description: Optional[str] = Field(default=None, description=f"{PLACEHOLDER_PREFIX}assistant_description")
    oid: Optional[int] = Field(default=1, description=f"{PLACEHOLDER_PREFIX}oid")
    enable_custom_model: Optional[bool] = Field(default=False, description=f"{PLACEHOLDER_PREFIX}oid")
    custom_model: Optional[str] = Field(description=f"{PLACEHOLDER_PREFIX}oid")


class AssistantDTO(AssistantBase, BaseCreatorDTO):
    pass


class AssistantHeader(AssistantDTO):
    unique: Optional[str] = None
    certificate: Optional[str] = None
    online: bool = False
    request_origin: Optional[str] = None


class AssistantValidator(BaseModel):
    valid: bool = False
    id_match: bool = False
    domain_match: bool = False
    token: Optional[str] = None

    def __init__(
            self,
            valid: bool = False,
            id_match: bool = False,
            domain_match: bool = False,
            token: Optional[str] = None,
            **kwargs
    ):
        super().__init__(
            valid=valid,
            id_match=id_match,
            domain_match=domain_match,
            token=token,
            **kwargs
        )


class AssistantFieldSchema(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    type: Optional[str] = None
    comment: Optional[str] = None


class AssistantTableSchema(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    comment: Optional[str] = None
    rule: Optional[str] = None
    sql: Optional[str] = None
    fields: Optional[list[AssistantFieldSchema]] = None


class AssistantOutDsBase(BaseModel):
    id: Optional[int] = None
    name: str
    type: Optional[str] = None
    type_name: Optional[str] = None
    comment: Optional[str] = None
    description: Optional[str] = None
    configuration: Optional[str] = None


class AssistantOutDsSchema(AssistantOutDsBase):
    host: Optional[str] = None
    port: Optional[int] = None
    dataBase: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    db_schema: Optional[str] = None
    extraParams: Optional[str] = None
    mode: Optional[str] = None
    tables: Optional[list[AssistantTableSchema]] = None


class AssistantUiSchema(BaseCreatorDTO):
    theme: Optional[str] = None
    header_font_color: Optional[str] = None
    logo: Optional[str] = None
    float_icon: Optional[str] = None
    float_icon_drag: Optional[bool] = False
    x_type: Optional[str] = 'right'
    x_val: Optional[int] = 0
    y_type: Optional[str] = 'bottom'
    y_val: Optional[int] = 33
    name: Optional[str] = None
    welcome: Optional[str] = None
    welcome_desc: Optional[str] = None

class ApikeyStatus(BaseModel):
    id: int = Field(description=f"{PLACEHOLDER_PREFIX}id")
    status: bool = Field(description=f"{PLACEHOLDER_PREFIX}status")

class ApikeyGridItem(BaseCreatorDTO):
    access_key: str = Field(description=f"Access Key")
    secret_key: str = Field(description=f"Secret Key")
    status: bool = Field(description=f"{PLACEHOLDER_PREFIX}status")
    create_time: int = Field(description=f"{PLACEHOLDER_PREFIX}create_time")
