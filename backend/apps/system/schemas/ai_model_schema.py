
"""AI Model DTO 旧导入路径兼容。"""

from apps.ai_model.models.dto import (
    AiModelConfigItem as AiModelConfigItem,
)
from apps.ai_model.models.dto import AiModelCreator as AiModelCreator
from apps.ai_model.models.dto import AiModelEditor as AiModelEditor
from apps.ai_model.models.dto import AiModelGridItem as AiModelGridItem
from apps.ai_model.models.dto import AiModelItem as AiModelItem

__all__ = [
    "AiModelConfigItem",
    "AiModelCreator",
    "AiModelEditor",
    "AiModelGridItem",
    "AiModelItem",
]
