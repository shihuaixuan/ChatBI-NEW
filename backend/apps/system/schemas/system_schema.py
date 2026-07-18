from typing import Optional

from pydantic import BaseModel, Field

from apps.access_control.models.dto import (
    EMAIL_REGEX as EMAIL_REGEX,
)
from apps.access_control.models.dto import (
    PWD_REGEX as PWD_REGEX,
)
from apps.access_control.models.dto import (
    ApikeyGridItem as ApikeyGridItem,
)
from apps.access_control.models.dto import (
    ApikeyStatus as ApikeyStatus,
)
from apps.access_control.models.dto import (
    BaseUser as BaseUser,
)
from apps.access_control.models.dto import (
    BaseUserDTO as BaseUserDTO,
)
from apps.access_control.models.dto import (
    PwdEditor as PwdEditor,
)
from apps.access_control.models.dto import (
    UserCreator as UserCreator,
)
from apps.access_control.models.dto import (
    UserEditor as UserEditor,
)
from apps.access_control.models.dto import (
    UserGrid as UserGrid,
)
from apps.access_control.models.dto import (
    UserInfoDTO as UserInfoDTO,
)
from apps.access_control.models.dto import (
    UserLanguage as UserLanguage,
)
from apps.access_control.models.dto import (
    UserStatus as UserStatus,
)
from apps.access_control.models.dto import (
    UserWs as UserWs,
)
from apps.access_control.models.dto import (
    UserWsBase as UserWsBase,
)
from apps.access_control.models.dto import (
    UserWsDTO as UserWsDTO,
)
from apps.access_control.models.dto import (
    UserWsEditor as UserWsEditor,
)
from apps.access_control.models.dto import (
    UserWsOption as UserWsOption,
)
from apps.access_control.models.dto import (
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
