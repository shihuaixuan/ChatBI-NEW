"""生成流程的知识上下文：SQL 示例、业务术语与自定义提示词。"""

import json
from typing import Protocol

from apps.chatbi.models.dto.generation_context import GenerationContextScope
from apps.chatbi.models.dto.generation_custom_prompt import (
    GenerationCustomPromptQuery,
    GenerationCustomPromptResult,
)
from apps.knowledge.services import SQLExampleQueryService
from apps.semantic.services import SemanticTermQueryService


class GenerationContextService:
    """统一构造 SQL 生成使用的知识上下文。"""

    def __init__(
        self,
        sql_example_service: SQLExampleQueryService,
        term_query_service: SemanticTermQueryService,
    ) -> None:
        self._sql_example_service = sql_example_service
        self._term_query_service = term_query_service

    def build_sql_examples(
        self,
        question: str,
        scope: GenerationContextScope,
    ) -> tuple[str, list[dict[str, str]]]:
        if scope.use_assistant_sql_examples:
            return self._sql_example_service.build_prompt(
                question,
                scope.workspace_id or 1,
                assistant_id=scope.sql_example_assistant_id,
            )
        return self._sql_example_service.build_prompt(
            question,
            scope.workspace_id or 1,
            datasource_id=scope.datasource_id,
        )

    def build_term_context(
        self,
        workspace_id: int,
        dataset_id: int | None,
        question: str,
    ) -> tuple[str, list[dict[str, object]]]:
        if dataset_id is None:
            return "", []
        results = self._term_query_service.search(
            workspace_id,
            dataset_id,
            question,
            limit=10,
        )
        items: list[dict[str, object]] = [
            {
                "words": list(result.words),
                "description": result.description or "",
            }
            for result in results
        ]
        if not items:
            return "", []
        return json.dumps(items, ensure_ascii=False), items


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
    "GenerationContextService",
    "GenerationCustomPromptProvider",
    "GenerationCustomPromptService",
]
