"""AI 模型配置持久化对象。"""

from sqlmodel import BigInteger, Field, Text

from common.core.models import SnowflakeBase


class AiModelBase:
    supplier: int = Field(nullable=False)
    name: str = Field(max_length=255, nullable=False)
    model_type: int = Field(nullable=False)
    base_model: str = Field(max_length=255, nullable=False)
    default_model: bool = Field(default=False, nullable=False)


class AiModelDetail(SnowflakeBase, AiModelBase, table=True):
    __tablename__ = "ai_model"

    api_key: str | None = Field(nullable=True)
    api_domain: str = Field(nullable=False)
    protocol: int = Field(nullable=False, default=1)
    config: str = Field(sa_type=Text)
    status: int = Field(nullable=False, default=1)
    create_time: int = Field(default=0, sa_type=BigInteger)
