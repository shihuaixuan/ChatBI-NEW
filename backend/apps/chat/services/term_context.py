"""Chat 对 Semantic 术语查询结果的上下文转换。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from apps.semantic.models.dto import TermSearchResult


class SemanticTermQuery(Protocol):
    """Chat 使用的 Semantic 术语查询最小契约。"""

    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[TermSearchResult]: ...


class ChatTermContextService:
    """把数据集范围内的 Semantic 术语转换为 Chat 提示上下文。"""

    def __init__(self, term_query: SemanticTermQuery) -> None:
        self._term_query = term_query

    def build(
        self,
        oid: int,
        dataset_id: int,
        question: str,
    ) -> tuple[str, list[dict[str, object]]]:
        results = self._term_query.search(oid, dataset_id, question, limit=10)
        items: list[dict[str, object]] = [
            {
                "words": list(result.words),
                "description": result.description,
            }
            for result in results
        ]
        if not items:
            return "", []
        return json.dumps(items, ensure_ascii=False), items


__all__ = ["ChatTermContextService", "SemanticTermQuery"]
