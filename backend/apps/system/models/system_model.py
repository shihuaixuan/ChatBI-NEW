from typing import Optional

from sqlmodel import BigInteger, Field, SQLModel, Text

# 当前 System API 与 XPack 仍使用旧导入路径，权威 ORM 已归属 Access Control。
from apps.access_control.models import ApiKeyBaseModel as ApiKeyBaseModel
from apps.access_control.models import ApiKeyModel as ApiKeyModel
from apps.access_control.models import (
    AuthenticationBaseModel as AuthenticationBaseModel,
)
from apps.access_control.models import AuthenticationModel as AuthenticationModel
from apps.access_control.models import UserWsModel as UserWsModel
from apps.access_control.models import WorkspaceBase as WorkspaceBase
from apps.access_control.models import WorkspaceEditor as WorkspaceEditor
from apps.access_control.models import WorkspaceModel as WorkspaceModel

# 当前 System API 与 XPack 仍使用旧导入路径，权威 ORM 已归属 AI Model。
from apps.ai_model.models import AiModelBase as AiModelBase
from apps.ai_model.models import AiModelDetail as AiModelDetail
from common.core.models import SnowflakeBase


class AssistantBaseModel(SQLModel):
    name: str = Field(max_length=255, nullable=False)
    type: int = Field(nullable=False, default=0)
    domain: str = Field(max_length=255, nullable=False)
    description: Optional[str] = Field(sa_type = Text(), nullable=True)
    configuration: Optional[str] = Field(sa_type = Text(), nullable=True)
    create_time: int = Field(default=0, sa_type=BigInteger())
    app_id: Optional[str] = Field(default=None, max_length=255,  nullable=True)
    app_secret: Optional[str] = Field(default=None, max_length=255, nullable=True)
    oid: Optional[int] = Field(nullable=True, sa_type=BigInteger(), default=1)
    enable_custom_model: Optional[bool] = Field(default=False, nullable=True)
    custom_model: Optional[str] = Field(default=None, max_length=255, nullable=True)


class AssistantModel(SnowflakeBase, AssistantBaseModel, table=True):
    __tablename__ = "sys_assistant"
