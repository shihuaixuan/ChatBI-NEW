from sqlmodel import SQLModel

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

# 当前 XPack 仍可能使用旧导入路径，权威 ORM 已归属 Assistant。
from apps.assistant import AssistantBaseModel as AssistantBaseModel
from apps.assistant import AssistantModel as AssistantModel
