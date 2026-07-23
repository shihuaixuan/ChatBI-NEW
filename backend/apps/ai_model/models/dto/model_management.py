"""AI 模型管理契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.core.schemas import BaseCreatorDTO


class AiModelItem(BaseModel):
    name: str = Field(description=f"{PLACEHOLDER_PREFIX}model_name")
    model_type: int = Field(description=f"{PLACEHOLDER_PREFIX}model_type")
    base_model: str = Field(description=f"{PLACEHOLDER_PREFIX}base_model")
    supplier: int = Field(description=f"{PLACEHOLDER_PREFIX}supplier")
    protocol: int = Field(description=f"{PLACEHOLDER_PREFIX}protocol")
    default_model: bool = Field(
        default=False,
        description=f"{PLACEHOLDER_PREFIX}default_model",
    )


class AiModelGridItem(AiModelItem, BaseCreatorDTO):
    pass


class AiModelConfigItem(BaseModel):
    key: str = Field(description=f"{PLACEHOLDER_PREFIX}arg_name")
    val: Any = Field(description=f"{PLACEHOLDER_PREFIX}arg_val")
    name: str = Field(description=f"{PLACEHOLDER_PREFIX}arg_show_name")


class AiModelCreator(AiModelItem):
    api_domain: str = Field(description=f"{PLACEHOLDER_PREFIX}api_domain")
    api_key: str = Field(description=f"{PLACEHOLDER_PREFIX}api_key")
    config_list: list[AiModelConfigItem] = Field(
        description=f"{PLACEHOLDER_PREFIX}config_list"
    )


class AiModelEditor(AiModelCreator, BaseCreatorDTO):
    pass


class AIModelRecord(BaseModel):
    """仓储返回的完整模型配置快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    model_type: int
    base_model: str
    supplier: int
    protocol: int
    default_model: bool
    api_domain: str
    api_key: str | None = None
    config: str | None = None
    status: int
    create_time: int


class AIModelCreateData(BaseModel):
    name: str
    model_type: int
    base_model: str
    supplier: int
    protocol: int
    default_model: bool
    api_domain: str
    api_key: str | None
    config: str
    create_time: int


class AIModelUpdateData(BaseModel):
    name: str
    model_type: int
    base_model: str
    supplier: int
    protocol: int
    api_domain: str
    api_key: str | None
    config: str


class AIModelSecretUpdate(BaseModel):
    model_id: int
    api_domain: str
    api_key: str | None
    supplier: int


class AIModelDeleteResult(StrEnum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    DEFAULT_MODEL = "default_model"
