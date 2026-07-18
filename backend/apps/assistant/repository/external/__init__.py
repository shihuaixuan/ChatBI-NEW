"""Assistant 外部仓储适配导出。"""

from apps.assistant.repository.external.http_datasource import (
    AssistantOutDs,
    AssistantOutDsFactory,
)

__all__ = [
    "AssistantOutDs",
    "AssistantOutDsFactory",
]
