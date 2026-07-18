"""面向跨领域调用方的 Semantic 术语查询服务。"""

from __future__ import annotations

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import TermSearchResult
from apps.semantic.repository.dataset_index_repository import DatasetSchemaReader
from apps.semantic.utils.text import unique_texts


class SemanticTermQueryService:
    """只从指定数据集的运行时 Schema 查询有效术语。"""

    def __init__(self, schema_reader: DatasetSchemaReader) -> None:
        self._schema_reader = schema_reader

    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[TermSearchResult]:
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return []
        if limit <= 0 or limit > 100:
            raise SemanticValidationError("SEMANTIC_TERM_SEARCH_LIMIT_INVALID")

        schema = self._schema_reader.build_dataset_schema(oid, dataset_id)
        ranked: list[tuple[int, int, TermSearchResult]] = []
        for position, term in enumerate(schema.terms):
            words = unique_texts([term.name, *term.alias])
            score = _match_score(normalized_query, words)
            if score is None:
                continue
            ranked.append(
                (
                    score,
                    position,
                    TermSearchResult(
                        term_id=term.id,
                        dataset_id=dataset_id,
                        words=words,
                        description=term.description,
                        related_assets=list(term.related_schema_elements),
                    ),
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[:limit]]


def _match_score(query: str, words: list[str]) -> int | None:
    normalized_words = [word.casefold() for word in words]
    if query in normalized_words:
        return 0
    if any(word in query for word in normalized_words):
        return 1
    if any(query in word for word in normalized_words):
        return 2
    return None


__all__ = ["SemanticTermQueryService"]
