"""AI 模型运行时配置仓储端口。"""

from typing import Protocol

from apps.ai_model.models.dto import (
    AIModelCreateData,
    AIModelDeleteResult,
    AIModelRecord,
    AIModelSecretUpdate,
    AIModelUpdateData,
    StoredAIModelConfig,
)


class AIModelConfigRepository(Protocol):
    def get_by_id(self, model_id: int) -> StoredAIModelConfig | None:
        """按 ID 读取模型配置。"""

    def get_default(self) -> StoredAIModelConfig | None:
        """读取系统默认模型配置。"""


class AIModelManagementRepository(Protocol):
    def list_models(self, keyword: str | None = None) -> list[AIModelRecord]:
        """按名称筛选并读取模型列表。"""

    def get_model(self, model_id: int) -> AIModelRecord | None:
        """读取单个模型。"""

    def has_default(self) -> bool:
        """判断是否已经配置默认模型。"""

    def create_model(self, data: AIModelCreateData) -> AIModelRecord:
        """创建模型，并在同一事务内维护默认模型。"""

    def update_model(
        self,
        model_id: int,
        data: AIModelUpdateData,
    ) -> AIModelRecord | None:
        """更新模型的普通配置。"""

    def set_default(self, model_id: int) -> AIModelRecord | None:
        """在同一事务内切换唯一默认模型。"""

    def delete_model(self, model_id: int) -> AIModelDeleteResult:
        """锁定目标后删除非默认模型。"""

    def apply_secret_updates(self, updates: list[AIModelSecretUpdate]) -> None:
        """在同一事务内应用启动期密钥迁移结果。"""
