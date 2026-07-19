"""SQL_EXEMPLAR profile 的活动 generation 查询执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from apps.retrieval.embedding import (
    EmbeddingProvider,
    default_retrieval_embedding_provider,
)
from apps.retrieval.errors import RetrievalConfigurationError, RetrievalError
from apps.retrieval.profiles import get_retrieval_profile
from apps.retrieval.schemas import (
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalProfileName,
)
from apps.retrieval.semantic_runtime import (
    ObservedEmbeddingProvider,
    RetrievalEmbeddingRuntimeConfig,
)
from common.core.config import settings


@dataclass(frozen=True, slots=True)
class SQLExampleRecallCandidate:
    """SQL 示例单通道候选。"""

    example_id: int
    channel: RetrievalChannel
    score: float
    index_generation: str


class SQLExampleRecallResult(BaseModel):
    """SQL_EXEMPLAR profile 的可观测召回结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    example_ids: tuple[int, ...] = ()
    channels: tuple[RetrievalChannelDiagnostic, ...] = ()
    index_generations: tuple[str, ...] = ()


class SQLExampleSearchStore:
    """只查询 SQL 示例来源当前活动 generation 的 PostgreSQL 适配器。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    def search_lexical(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
        limit: int,
    ) -> list[SQLExampleRecallCandidate]:
        return self._search(
            _SQL_EXAMPLE_LEXICAL_SQL,
            workspace_id,
            question,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
            limit=limit,
            channel=RetrievalChannel.LEXICAL,
            extra={"lexical_threshold": 0.3},
        )

    def search_dense(
        self,
        workspace_id: int,
        question: str,
        vector: list[float],
        *,
        datasource_id: int | None,
        assistant_id: int | None,
        embedding_profile: str,
        limit: int,
    ) -> list[SQLExampleRecallCandidate]:
        return self._search(
            _SQL_EXAMPLE_DENSE_SQL,
            workspace_id,
            question,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
            limit=limit,
            channel=RetrievalChannel.DENSE,
            extra={
                "embedding_profile": embedding_profile,
                "query_vector": str(vector),
            },
        )

    def _search(
        self,
        statement: Any,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
        limit: int,
        channel: RetrievalChannel,
        extra: dict[str, Any],
    ) -> list[SQLExampleRecallCandidate]:
        if limit <= 0:
            raise ValueError("SQL_EXEMPLAR_RECALL_LIMIT_INVALID")
        rows = self._session.execute(
            statement,
            {
                "tenant_id": workspace_id,
                "source_key": f"workspace:{workspace_id}:sql-examples",
                "query_text": question,
                "normalized_query": _normalize_text(question),
                "datasource_id": datasource_id,
                "assistant_id": assistant_id,
                "limit": limit,
                **extra,
            },
        ).mappings().all()
        candidates: list[SQLExampleRecallCandidate] = []
        for row in rows:
            try:
                example_id = int(str(row["source_resource_id"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("SQL_EXEMPLAR_SOURCE_RESOURCE_ID_INVALID") from exc
            candidates.append(
                SQLExampleRecallCandidate(
                    example_id=example_id,
                    channel=channel,
                    score=float(row["score"]),
                    index_generation=str(row["index_generation"]),
                )
            )
        return candidates


class SQLExampleRetriever:
    """执行 SQL_EXEMPLAR profile，并为 Knowledge 兼容入口返回稳定示例 ID。"""

    def __init__(
        self,
        session: Any,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_config: RetrievalEmbeddingRuntimeConfig | None = None,
        store: SQLExampleSearchStore | None = None,
        query_timeout_ms: int | None = None,
    ) -> None:
        self._session = session
        self._store = store or SQLExampleSearchStore(session)
        self._embedding_config = (
            embedding_config or RetrievalEmbeddingRuntimeConfig.from_settings(settings)
        )
        self._embedding_provider = embedding_provider
        self._query_timeout_ms = (
            settings.RETRIEVAL_QUERY_TIMEOUT_MS
            if query_timeout_ms is None
            else query_timeout_ms
        )
        if self._query_timeout_ms <= 0:
            raise ValueError("SQL_EXEMPLAR_QUERY_TIMEOUT_INVALID")
        self._embedding_configuration_error: RetrievalConfigurationError | None = None
        if self._embedding_config.enabled:
            try:
                self._embedding_config.validate()
            except RetrievalConfigurationError as exc:
                if not self._embedding_config.allow_lexical_fallback:
                    raise
                self._embedding_configuration_error = exc

    def search_ids(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> list[int]:
        """兼容 Knowledge 查询端口，内部仍按 SQL_EXEMPLAR profile 执行。"""

        return list(
            self.search(
                workspace_id,
                question,
                datasource_id=datasource_id,
                assistant_id=assistant_id,
            ).example_ids
        )

    def search(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
    ) -> SQLExampleRecallResult:
        normalized_question = question.strip()
        if workspace_id <= 0:
            raise ValueError("SQL_EXEMPLAR_WORKSPACE_ID_INVALID")
        if not normalized_question or (datasource_id is None and assistant_id is None):
            return SQLExampleRecallResult()
        if datasource_id is not None and datasource_id <= 0:
            raise ValueError("SQL_EXEMPLAR_DATASOURCE_ID_INVALID")
        if assistant_id is not None and assistant_id <= 0:
            raise ValueError("SQL_EXEMPLAR_ASSISTANT_ID_INVALID")

        profile = get_retrieval_profile(RetrievalProfileName.SQL_EXEMPLAR)
        self._set_database_timeout()
        lexical = self._store.search_lexical(
            workspace_id,
            normalized_question,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
            limit=profile.recall_limits[RetrievalChannel.LEXICAL],
        )
        channels = [
            RetrievalChannelDiagnostic(
                channel=RetrievalChannel.LEXICAL,
                status=RetrievalChannelStatus.SUCCEEDED,
                candidate_count=len(lexical),
            )
        ]
        candidates = {RetrievalChannel.LEXICAL: lexical}
        dense, dense_diagnostic = self._dense_candidates(
            workspace_id,
            normalized_question,
            datasource_id=datasource_id,
            assistant_id=assistant_id,
            limit=profile.recall_limits[RetrievalChannel.DENSE],
        )
        channels.append(dense_diagnostic)
        if dense:
            candidates[RetrievalChannel.DENSE] = dense
        fused = _fuse_candidates(
            candidates,
            rrf_k=profile.rrf_k or 60,
            limit=profile.result_limit,
        )
        generations = sorted(
            {candidate.index_generation for values in candidates.values() for candidate in values}
        )
        return SQLExampleRecallResult(
            example_ids=tuple(item.example_id for item in fused),
            channels=tuple(channels),
            index_generations=tuple(generations),
        )

    def _dense_candidates(
        self,
        workspace_id: int,
        question: str,
        *,
        datasource_id: int | None,
        assistant_id: int | None,
        limit: int,
    ) -> tuple[list[SQLExampleRecallCandidate], RetrievalChannelDiagnostic]:
        if not self._embedding_config.enabled:
            return [], RetrievalChannelDiagnostic(
                channel=RetrievalChannel.DENSE,
                status=RetrievalChannelStatus.SKIPPED,
            )
        if self._embedding_configuration_error is not None:
            return [], RetrievalChannelDiagnostic(
                channel=RetrievalChannel.DENSE,
                status=RetrievalChannelStatus.UNAVAILABLE,
                error_code=str(
                    self._embedding_configuration_error.details.get("reason_code")
                    or self._embedding_configuration_error.code
                ),
            )

        provider = self._embedding_provider or default_retrieval_embedding_provider()
        observed_provider = ObservedEmbeddingProvider(
            provider,
            self._embedding_config.dimension,
        )
        try:
            vector = observed_provider.embed_query(question)
            candidates = self._store.search_dense(
                workspace_id,
                question,
                vector,
                datasource_id=datasource_id,
                assistant_id=assistant_id,
                embedding_profile="bge-m3-1024",
                limit=limit,
            )
        except RetrievalError as exc:
            if not self._embedding_config.allow_lexical_fallback:
                raise
            status = (
                RetrievalChannelStatus.UNAVAILABLE
                if isinstance(exc, RetrievalConfigurationError)
                else RetrievalChannelStatus.FAILED
            )
            return [], RetrievalChannelDiagnostic(
                channel=RetrievalChannel.DENSE,
                status=status,
                error_code=str(exc.details.get("reason_code") or exc.code),
            )
        return candidates, RetrievalChannelDiagnostic(
            channel=RetrievalChannel.DENSE,
            status=RetrievalChannelStatus.SUCCEEDED,
            candidate_count=len(candidates),
        )

    def _set_database_timeout(self) -> None:
        """PostgreSQL 查询使用事务级硬超时，Fake Store 不需要数据库绑定。"""

        get_bind = getattr(self._session, "get_bind", None)
        if get_bind is None:
            return
        bind = get_bind()
        if bind.dialect.name != "postgresql":
            return
        self._session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{self._query_timeout_ms}ms"},
        )


def _fuse_candidates(
    candidates_by_channel: dict[RetrievalChannel, list[SQLExampleRecallCandidate]],
    *,
    rrf_k: int,
    limit: int,
) -> list[SQLExampleRecallCandidate]:
    if rrf_k <= 0 or limit <= 0:
        raise ValueError("SQL_EXEMPLAR_RRF_CONFIG_INVALID")
    scores: dict[int, float] = {}
    candidates: dict[int, SQLExampleRecallCandidate] = {}
    for channel_candidates in candidates_by_channel.values():
        seen: set[int] = set()
        rank = 0
        for candidate in channel_candidates:
            if candidate.example_id in seen:
                continue
            seen.add(candidate.example_id)
            rank += 1
            candidates.setdefault(candidate.example_id, candidate)
            scores[candidate.example_id] = scores.get(candidate.example_id, 0) + 1 / (
                rrf_k + rank
            )
    ordered_ids = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
    return [candidates[item] for item in ordered_ids]


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


_SQL_EXAMPLE_SCOPED_CTE = """
WITH scoped AS (
    SELECT
        s.active_generation AS index_generation,
        r.source_resource_id,
        r.title AS resource_title,
        u.id AS unit_id,
        u.title AS unit_title,
        u.content,
        u.contextual_text,
        (r.title || ' ' || u.title || ' ' || u.content || ' ' || u.contextual_text)
            AS lexical_document
    FROM retrieval_source AS s
    JOIN retrieval_index_generation AS g
      ON g.tenant_id = s.tenant_id
     AND g.source_id = s.id
     AND g.generation = s.active_generation
     AND g.status = 'active'
    JOIN retrieval_resource AS r
      ON r.tenant_id = s.tenant_id
     AND r.source_id = s.id
     AND r.status = 'active'
     AND r.resource_type = 'SQL_EXEMPLAR'
    JOIN retrieval_unit AS u
      ON u.tenant_id = r.tenant_id
     AND u.resource_id = r.id
     AND u.index_generation = s.active_generation
     AND u.status = 'active'
    WHERE s.tenant_id = :tenant_id
      AND s.source_type = 'sql_exemplar'
      AND s.source_key = :source_key
      AND s.status = 'active'
      AND (
          (
              CAST(:assistant_id AS bigint) IS NOT NULL
              AND r.metadata ->> 'assistant_id' = CAST(:assistant_id AS text)
          )
          OR (
              CAST(:assistant_id AS bigint) IS NULL
              AND CAST(:datasource_id AS bigint) IS NOT NULL
              AND r.metadata ->> 'datasource_id' = CAST(:datasource_id AS text)
          )
      )
)
"""


_SQL_EXAMPLE_LEXICAL_SQL = text(
    _SQL_EXAMPLE_SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*,
           GREATEST(
               similarity(resource_title, :query_text),
               similarity(lexical_document, :query_text),
               CASE WHEN position(:normalized_query IN lower(lexical_document)) > 0
                    THEN 0.99 ELSE 0 END
           )::double precision AS score
    FROM scoped
    WHERE position(:normalized_query IN lower(lexical_document)) > 0
       OR (
           (resource_title % :query_text OR lexical_document % :query_text)
           AND GREATEST(
               similarity(resource_title, :query_text),
               similarity(lexical_document, :query_text)
           ) >= :lexical_threshold
       )
), collapsed AS (
    SELECT DISTINCT ON (source_resource_id) *
    FROM ranked
    ORDER BY source_resource_id, score DESC, unit_id
)
SELECT source_resource_id, index_generation, score
FROM collapsed
ORDER BY score DESC, source_resource_id
LIMIT :limit
"""
)


_SQL_EXAMPLE_DENSE_SQL = text(
    _SQL_EXAMPLE_SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*,
           (1 - (e.embedding <=> :query_vector))::double precision AS score
    FROM scoped
    JOIN retrieval_embedding AS e
      ON e.tenant_id = :tenant_id
     AND e.unit_id = scoped.unit_id
     AND e.index_generation = scoped.index_generation
     AND e.embedding_profile = :embedding_profile
     AND e.status = 'active'
     AND e.embedding IS NOT NULL
), collapsed AS (
    SELECT DISTINCT ON (source_resource_id) *
    FROM ranked
    ORDER BY source_resource_id, score DESC, unit_id
)
SELECT source_resource_id, index_generation, score
FROM collapsed
ORDER BY score DESC, source_resource_id
LIMIT :limit
"""
)


__all__ = [
    "SQLExampleRecallCandidate",
    "SQLExampleRecallResult",
    "SQLExampleRetriever",
    "SQLExampleSearchStore",
]
