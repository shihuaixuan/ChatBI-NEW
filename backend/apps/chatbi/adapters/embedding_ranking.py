import json
import math

from apps.ai_model.embedding import EmbeddingModelCache
from apps.chatbi.models import (
    DatasourceSelectionRankingCandidate,
    GenerationSchemaTableCandidate,
)


def _rank_candidates(
    question: str,
    candidates: list[tuple[int, str | None]],
    *,
    limit: int,
) -> list[int]:
    """按同一套向量规则排序 ChatBI 候选。"""

    query_embedding = EmbeddingModelCache.get_model().embed_query(question)
    ranked: list[tuple[float, int, int]] = []
    for position, (candidate_id, embedding) in enumerate(candidates):
        score = 0.0
        if embedding:
            raw_embedding = json.loads(embedding)
            if not isinstance(raw_embedding, list):
                raise ValueError("CHATBI_CANDIDATE_EMBEDDING_INVALID")
            candidate_embedding = [float(value) for value in raw_embedding]
            score = _cosine_similarity(query_embedding, candidate_embedding)
        ranked.append((score, position, candidate_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:limit]]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("CHATBI_CANDIDATE_EMBEDDING_DIMENSION_MISMATCH")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    ) / (left_norm * right_norm)


class EmbeddingSchemaRankingClient:
    """使用系统 Embedding 模型排序物理表。"""

    def rank(
        self,
        question: str,
        candidates: list[GenerationSchemaTableCandidate],
        *,
        limit: int,
    ) -> list[int]:
        return _rank_candidates(
            question,
            [
                (candidate.table_id, candidate.embedding)
                for candidate in candidates
            ],
            limit=limit,
        )


class EmbeddingDatasourceSelectionCandidateRanker:
    """使用系统 Embedding 模型排序本地数据源候选。"""

    def rank(
        self,
        question: str,
        candidates: list[DatasourceSelectionRankingCandidate],
        *,
        limit: int,
    ) -> list[int]:
        return _rank_candidates(
            question,
            [(candidate.id, candidate.embedding) for candidate in candidates],
            limit=limit,
        )


__all__ = [
    "EmbeddingDatasourceSelectionCandidateRanker",
    "EmbeddingSchemaRankingClient",
]
