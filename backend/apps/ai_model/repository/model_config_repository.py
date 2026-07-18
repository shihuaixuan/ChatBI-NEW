"""AI 模型运行时配置仓储端口。"""

from typing import Protocol

from apps.ai_model.models.dto import StoredAIModelConfig


class AIModelConfigRepository(Protocol):
    def get_by_id(self, model_id: int) -> StoredAIModelConfig | None:
        """按 ID 读取模型配置。"""

    def get_default(self) -> StoredAIModelConfig | None:
        """读取系统默认模型配置。"""
