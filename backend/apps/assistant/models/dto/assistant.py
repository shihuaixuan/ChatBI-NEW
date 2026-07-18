"""Assistant 领域 DTO。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO


class AssistantBase(BaseModel):
    name: str = Field(description=f"{PLACEHOLDER_PREFIX}model_name")
    domain: str = Field(description=f"{PLACEHOLDER_PREFIX}assistant_domain")
    type: int = Field(
        default=0,
        description=f"{PLACEHOLDER_PREFIX}assistant_type",
    )  # 0 普通助手，1 高级助手，2/3 为兼容类型，4 页面嵌入
    configuration: str | None = Field(
        default=None,
        description=f"{PLACEHOLDER_PREFIX}assistant_configuration",
    )
    description: str | None = Field(
        default=None,
        description=f"{PLACEHOLDER_PREFIX}assistant_description",
    )
    oid: int | None = Field(default=1, description=f"{PLACEHOLDER_PREFIX}oid")
    enable_custom_model: bool | None = Field(
        default=False,
        description=f"{PLACEHOLDER_PREFIX}oid",
    )
    custom_model: str | None = Field(
        default=None,
        description=f"{PLACEHOLDER_PREFIX}oid",
    )


class AssistantDTO(AssistantBase, BaseCreatorDTO):
    pass


class AssistantRecord(AssistantDTO):
    create_time: int = 0
    app_id: str | None = None
    app_secret: str | None = None


class AssistantPublicInfo(AssistantDTO):
    """公开嵌入接口返回的助手信息，不包含应用密钥。"""

    create_time: int = 0
    app_id: str | None = None


class AssistantHeader(AssistantDTO):
    unique: str | None = None
    certificate: str | None = None
    online: bool = False
    request_origin: str | None = None


class AssistantValidator(BaseModel):
    valid: bool = False
    id_match: bool = False
    domain_match: bool = False
    token: str | None = None


class AssistantUiSchema(BaseCreatorDTO):
    theme: str | None = None
    header_font_color: str | None = None
    logo: str | None = None
    float_icon: str | None = None
    float_icon_drag: bool | None = False
    x_type: str | None = "right"
    x_val: int | None = 0
    y_type: str | None = "bottom"
    y_val: int | None = 33
    name: str | None = None
    welcome: str | None = None
    welcome_desc: str | None = None


class AssistantCreateData(BaseModel):
    name: str
    domain: str
    type: int
    configuration: str | None
    description: str | None
    create_time: int
    app_id: str | None
    app_secret: str | None
    oid: int
    enable_custom_model: bool
    custom_model: str | None


class AssistantUpdateData(BaseModel):
    name: str
    domain: str
    type: int
    configuration: str | None
    description: str | None
    oid: int
    enable_custom_model: bool
    custom_model: str | None


class AssistantReference(BaseModel):
    id: int
    name: str


class AssistantUiUpdateResult(BaseModel):
    assistant: AssistantRecord
    obsolete_asset_ids: list[str]
