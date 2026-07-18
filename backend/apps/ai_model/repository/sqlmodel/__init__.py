"""AI Model SQLModel 仓储实现。"""

from apps.ai_model.repository.sqlmodel.model_config_repository import (
    SQLModelAIModelConfigRepository,
)
from apps.ai_model.repository.sqlmodel.model_management_repository import (
    SQLModelAIModelManagementRepository,
)

__all__ = [
    "SQLModelAIModelConfigRepository",
    "SQLModelAIModelManagementRepository",
]
