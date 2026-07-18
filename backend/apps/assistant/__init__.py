"""Assistant 领域公开模型。"""

from apps.assistant.models import (
    AssistantBase,
    AssistantBaseModel,
    AssistantDTO,
    AssistantHeader,
    AssistantModel,
    AssistantPublicInfo,
    AssistantRecord,
    AssistantReference,
    AssistantUiSchema,
    AssistantUiUpdateResult,
    AssistantValidator,
)
from apps.datasource import (
    ExternalDatasource as AssistantOutDsSchema,
)
from apps.datasource import (
    ExternalDatasourceField as AssistantFieldSchema,
)
from apps.datasource import (
    ExternalDatasourceTable as AssistantTableSchema,
)

AssistantOutDsBase = AssistantOutDsSchema

__all__ = [
    "AssistantBase",
    "AssistantBaseModel",
    "AssistantDTO",
    "AssistantFieldSchema",
    "AssistantHeader",
    "AssistantModel",
    "AssistantOutDsBase",
    "AssistantOutDsSchema",
    "AssistantPublicInfo",
    "AssistantRecord",
    "AssistantReference",
    "AssistantTableSchema",
    "AssistantUiSchema",
    "AssistantUiUpdateResult",
    "AssistantValidator",
]
