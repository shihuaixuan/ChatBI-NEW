from typing import Optional

from sqlmodel import BigInteger, Field, SQLModel, Text

# 当前 System API 与 XPack 仍使用旧导入路径，权威 ORM 已归属 AI Model。
from apps.ai_model.models import AiModelBase as AiModelBase
from apps.ai_model.models import AiModelDetail as AiModelDetail
from common.core.models import SnowflakeBase
from common.core.schemas import BaseCreatorDTO


class WorkspaceBase(SQLModel):
    name: str = Field(max_length=255, nullable=False)

class WorkspaceEditor(WorkspaceBase, BaseCreatorDTO):
    pass
    
class WorkspaceModel(SnowflakeBase, WorkspaceBase, table=True):
    __tablename__ = "sys_workspace"
    create_time: int = Field(default=0, sa_type=BigInteger())
    
class UserWsBaseModel(SQLModel):
    uid: int = Field(nullable=False, sa_type=BigInteger())
    oid: int = Field(nullable=False, sa_type=BigInteger())
    weight: int =  Field(default=0, nullable=False)
    
class UserWsModel(SnowflakeBase, UserWsBaseModel, table=True):
    __tablename__ = "sys_user_ws"
    

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
    

class AuthenticationBaseModel(SQLModel):
    name: str = Field(max_length=255, nullable=False)
    type: int = Field(nullable=False, default=0)
    config: Optional[str] = Field(sa_type = Text(), nullable=True)
    
    
class AuthenticationModel(SnowflakeBase, AuthenticationBaseModel, table=True):
    __tablename__ = "sys_authentication"
    create_time: Optional[int] = Field(default=0, sa_type=BigInteger())
    enable: bool = Field(default=False, nullable=False)
    valid: bool = Field(default=False, nullable=False)
    

class ApiKeyBaseModel(SQLModel):
    access_key: str = Field(max_length=255, nullable=False)
    secret_key: str = Field(max_length=255, nullable=False)
    create_time: int = Field(default=0, sa_type=BigInteger())
    uid: int = Field(default=0,nullable=False, sa_type=BigInteger())
    status: bool = Field(default=True, nullable=False)
    
class ApiKeyModel(SnowflakeBase, ApiKeyBaseModel, table=True):
    __tablename__ = "sys_apikey"
