"""生成流程的知识上下文：SQL 示例与业务术语。"""

import json

from apps.chatbi.models.dto.generation_context import GenerationContextScope
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


__all__ = ["GenerationContextService"]
