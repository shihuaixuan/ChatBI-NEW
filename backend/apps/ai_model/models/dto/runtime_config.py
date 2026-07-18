"""AI 模型运行时配置契约。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StoredAIModelConfig(BaseModel):
    """仓储返回的模型配置快照。"""

    model_id: int
    protocol: int
    base_model: str
    api_key: str | None = None
    api_domain: str
    config: str | None = None


class LLMConfig(BaseModel):
    """创建大模型客户端所需的稳定配置。"""

    model_config = ConfigDict(frozen=True)

    model_id: int | None = None
    model_type: str
    model_name: str
    api_key: str | None = None
    api_base_url: str | None = None
    additional_params: dict[str, Any] = Field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(
            (
                self.model_id,
                self.model_type,
                self.model_name,
                self.api_key,
                self.api_base_url,
                _freeze(self.additional_params),
            )
        )


def _freeze(value: Any) -> Any:
    """把 JSON 配置转换为可哈希结构，供客户端缓存使用。"""

    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
