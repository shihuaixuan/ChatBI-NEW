from typing import Protocol

from apps.chatbi.models.dto.generation_custom_prompt import (
    GenerationCustomPromptQuery,
    GenerationCustomPromptResult,
)


class GenerationCustomPromptProvider(Protocol):
    def is_enabled(self) -> bool: ...

    def find(
        self,
        query: GenerationCustomPromptQuery,
    ) -> GenerationCustomPromptResult: ...


class GenerationCustomPromptService:
    """通过稳定端口读取生成流程使用的自定义提示词。"""

    def __init__(self, provider: GenerationCustomPromptProvider) -> None:
        self._provider = provider
        self._enabled = provider.is_enabled()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def query(
        self,
        query: GenerationCustomPromptQuery,
    ) -> GenerationCustomPromptResult:
        if not self._enabled:
            return GenerationCustomPromptResult()
        return self._provider.find(query)


__all__ = [
    "GenerationCustomPromptProvider",
    "GenerationCustomPromptService",
]
