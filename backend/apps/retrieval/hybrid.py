"""统一检索新 generation 上的分槽多通道召回与 RRF 融合。"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from apps.retrieval.errors import (
    RetrievalConfigurationError,
    RetrievalDimensionMismatchError,
    RetrievalError,
    RetrievalIndexUnavailableError,
)
from apps.retrieval.planner import RetrievalQueryPlan, SemanticBindingQueryPlanner
from apps.retrieval.profiles import get_retrieval_profile
from apps.retrieval.schemas import (
    AssetReference,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalHit,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScores,
    RetrievalSourceType,
    RetrievalSubQuery,
)


class QueryEmbeddingProvider(Protocol):
    """查询侧只依赖单文本 embedding 端口。"""

    provider: str
    model: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        """返回查询文本向量。"""


class SemanticBindingRecallStore(Protocol):
    """混合策略依赖的存储召回端口。"""

    def search_exact(self, request: RetrievalRequest, subquery: RetrievalSubQuery, limit: int) -> list[RecallCandidate]: ...

    def search_alias(self, request: RetrievalRequest, subquery: RetrievalSubQuery, limit: int) -> list[RecallCandidate]: ...

    def search_lexical(self, request: RetrievalRequest, subquery: RetrievalSubQuery, limit: int) -> list[RecallCandidate]: ...

    def search_dense(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        vector: list[float],
        limit: int,
    ) -> list[RecallCandidate]: ...


@dataclass(frozen=True, slots=True)
class HybridRetrievalConfig:
    """一次 shadow 检索使用的不可变运行配置。"""

    embedding_profile: str = "bge-m3-1024"
    embedding_dimension: int = 1024
    dense_enabled: bool = True
    lexical_threshold: float = 0.3

    def __post_init__(self) -> None:
        if not self.embedding_profile.strip():
            raise ValueError("embedding_profile 不能为空")
        if self.embedding_dimension != 1024:
            raise ValueError("当前统一检索物理向量固定为 1024 维")
        if not 0.3 <= self.lexical_threshold <= 1:
            raise ValueError("lexical_threshold 必须位于 0.3 到 1，以保持 pg_trgm 索引语义一致")


@dataclass(frozen=True, slots=True)
class RecallCandidate:
    """单通道返回的资源候选及原始通道分数。"""

    channel: RetrievalChannel
    score: float
    hit: RetrievalHit


class SubQueryRecallResult(BaseModel):
    """一个槽位的融合候选和通道执行事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subquery: RetrievalSubQuery
    hits: tuple[RetrievalHit, ...] = ()
    channels: tuple[RetrievalChannelDiagnostic, ...] = ()
    fast_path: bool = False


class HybridRecallResult(BaseModel):
    """P1-4 shadow 输出；P1-5 才负责决策和编译白名单。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    plan: RetrievalQueryPlan
    slots: tuple[SubQueryRecallResult, ...]
    index_generations: tuple[str, ...] = ()
    total_latency_ms: float = Field(default=0, ge=0)


class SemanticBindingSearchStore:
    """PostgreSQL/pgvector 检索适配器，所有通道共用同一组硬过滤。"""

    def __init__(self, session: Any, config: HybridRetrievalConfig) -> None:
        self._session = session
        self._config = config

    def search_exact(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        limit: int,
    ) -> list[RecallCandidate]:
        return self._search(
            _EXACT_SQL,
            request,
            subquery,
            RetrievalChannel.EXACT,
            limit,
        )

    def search_alias(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        limit: int,
    ) -> list[RecallCandidate]:
        return self._search(
            _ALIAS_SQL,
            request,
            subquery,
            RetrievalChannel.ALIAS,
            limit,
        )

    def search_lexical(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        limit: int,
    ) -> list[RecallCandidate]:
        return self._search(
            _LEXICAL_SQL,
            request,
            subquery,
            RetrievalChannel.LEXICAL,
            limit,
            extra={"lexical_threshold": self._config.lexical_threshold},
        )

    def search_dense(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        vector: list[float],
        limit: int,
    ) -> list[RecallCandidate]:
        if len(vector) != self._config.embedding_dimension:
            raise RetrievalDimensionMismatchError(
                "查询向量与统一索引维度不一致",
                details={
                    "reason_code": "EMBEDDING_DIMENSION_MISMATCH",
                    "expected_dimension": self._config.embedding_dimension,
                    "actual_dimension": len(vector),
                },
            )
        return self._search(
            _DENSE_SQL,
            request,
            subquery,
            RetrievalChannel.DENSE,
            limit,
            extra={
                "embedding_profile": self._config.embedding_profile,
                "query_vector": str(vector),
            },
        )

    def _search(
        self,
        statement: Any,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        channel: RetrievalChannel,
        limit: int,
        *,
        extra: dict[str, Any] | None = None,
    ) -> list[RecallCandidate]:
        parameters = self._scope_parameters(request, subquery, limit)
        parameters.update(extra or {})
        rows = self._session.execute(statement, parameters).mappings().all()
        return [_candidate_from_row(dict(row), channel) for row in rows]

    @staticmethod
    def _scope_parameters(
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        limit: int,
    ) -> dict[str, Any]:
        resource_types = _resource_types_for_purpose(subquery.purpose)
        if limit <= 0:
            raise ValueError("检索通道 limit 必须大于 0")
        return {
            "tenant_id": request.tenant_id,
            "actor_id": request.actor_id,
            "principal_roles": request.scope.principal_roles,
            "principal_role_ids": [str(value) for value in request.scope.principal_role_ids],
            "dataset_scope": bool(request.scope.dataset_ids),
            "dataset_ids": request.scope.dataset_ids,
            "knowledge_scope": bool(request.scope.knowledge_base_ids),
            "knowledge_base_ids": request.scope.knowledge_base_ids,
            "source_scope": bool(request.scope.source_ids),
            "source_ids": request.scope.source_ids,
            "permission_version": request.scope.permission_version,
            "resource_types": [item.value for item in resource_types],
            "normalized_query": _normalize_text(subquery.text),
            "query_text": subquery.text,
            "limit": limit,
        }


class SemanticBindingHybridRetriever:
    """执行 P1-4 召回；不包含 P1-5 的候选门控和资产放行。"""

    def __init__(
        self,
        session: Any,
        *,
        planner: SemanticBindingQueryPlanner | None = None,
        embedding_provider: QueryEmbeddingProvider | None = None,
        config: HybridRetrievalConfig | None = None,
        store: SemanticBindingRecallStore | None = None,
    ) -> None:
        self._config = config or HybridRetrievalConfig()
        self._planner = planner or SemanticBindingQueryPlanner()
        self._provider = embedding_provider
        self._store = store or SemanticBindingSearchStore(session, self._config)
        if self._config.dense_enabled and self._provider is not None:
            if self._provider.dimension != self._config.embedding_dimension:
                raise RetrievalConfigurationError(
                    "查询 embedding provider 与统一索引维度不一致",
                    details={"reason_code": "EMBEDDING_DIMENSION_MISMATCH"},
                )

    def retrieve(self, request: RetrievalRequest) -> HybridRecallResult:
        if request.profiles != [RetrievalProfileName.SEMANTIC_BINDING]:
            raise ValueError("SEMANTIC_BINDING_HYBRID_PROFILE_MISMATCH")
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        if request.strategy_version != profile.version:
            raise ValueError("SEMANTIC_BINDING_HYBRID_STRATEGY_VERSION_MISMATCH")
        if not request.scope.dataset_ids:
            raise ValueError("SEMANTIC_BINDING_DATASET_SCOPE_REQUIRED")

        started = perf_counter()
        plan = self._planner.plan(request)
        slot_results = tuple(
            self._recall_slot(request, subquery, profile.rrf_k or 60, profile.result_limit)
            for subquery in plan.subqueries
        )
        generations = sorted(
            {
                str(hit.provenance["index_generation"])
                for slot in slot_results
                for hit in slot.hits
                if hit.provenance.get("index_generation")
            }
        )
        return HybridRecallResult(
            request_id=request.request_id,
            plan=plan,
            slots=slot_results,
            index_generations=tuple(generations),
            total_latency_ms=(perf_counter() - started) * 1000,
        )

    def _recall_slot(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        rrf_k: int,
        result_limit: int,
    ) -> SubQueryRecallResult:
        profile = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
        candidates: dict[RetrievalChannel, list[RecallCandidate]] = {}
        diagnostics: list[RetrievalChannelDiagnostic] = []

        exact, exact_diagnostic = self._run_channel(
            RetrievalChannel.EXACT,
            lambda: self._store.search_exact(
                request,
                subquery,
                profile.recall_limits[RetrievalChannel.EXACT],
            ),
        )
        candidates[RetrievalChannel.EXACT] = exact
        diagnostics.append(exact_diagnostic)

        aliases, alias_diagnostic = self._run_channel(
            RetrievalChannel.ALIAS,
            lambda: self._store.search_alias(
                request,
                subquery,
                profile.recall_limits[RetrievalChannel.ALIAS],
            ),
        )
        candidates[RetrievalChannel.ALIAS] = aliases
        diagnostics.append(alias_diagnostic)

        fast_path = len({item.hit.resource_id for item in [*exact, *aliases]}) == 1 and bool(
            exact or aliases
        )
        if fast_path:
            diagnostics.extend(
                self._skipped_diagnostics(
                    RetrievalChannel.LEXICAL,
                    RetrievalChannel.DENSE,
                    RetrievalChannel.RELATION,
                )
            )
            return SubQueryRecallResult(
                subquery=subquery,
                hits=tuple(reciprocal_rank_fusion(candidates, rrf_k=rrf_k, limit=result_limit)),
                channels=tuple(diagnostics),
                fast_path=True,
            )

        lexical, lexical_diagnostic = self._run_channel(
            RetrievalChannel.LEXICAL,
            lambda: self._store.search_lexical(
                request,
                subquery,
                profile.recall_limits[RetrievalChannel.LEXICAL],
            ),
        )
        candidates[RetrievalChannel.LEXICAL] = lexical
        diagnostics.append(lexical_diagnostic)

        if not self._config.dense_enabled:
            diagnostics.append(_skipped_diagnostic(RetrievalChannel.DENSE))
        elif self._provider is None:
            diagnostics.append(
                RetrievalChannelDiagnostic(
                    channel=RetrievalChannel.DENSE,
                    status=RetrievalChannelStatus.UNAVAILABLE,
                    error_code="EMBEDDING_PROVIDER_MISSING",
                )
            )
        else:
            dense, dense_diagnostic = self._run_dense(request, subquery, profile)
            candidates[RetrievalChannel.DENSE] = dense
            diagnostics.append(dense_diagnostic)

        diagnostics.append(_skipped_diagnostic(RetrievalChannel.RELATION))
        return SubQueryRecallResult(
            subquery=subquery,
            hits=tuple(reciprocal_rank_fusion(candidates, rrf_k=rrf_k, limit=result_limit)),
            channels=tuple(diagnostics),
            fast_path=False,
        )

    def _run_dense(
        self,
        request: RetrievalRequest,
        subquery: RetrievalSubQuery,
        profile: Any,
    ) -> tuple[list[RecallCandidate], RetrievalChannelDiagnostic]:
        if self._provider is None:
            raise AssertionError("dense provider 尚未配置")
        started = perf_counter()
        try:
            vector = self._provider.embed_query(subquery.text)
            candidates = self._store.search_dense(
                request,
                subquery,
                vector,
                profile.recall_limits[RetrievalChannel.DENSE],
            )
        except RetrievalError as exc:
            status = (
                RetrievalChannelStatus.UNAVAILABLE
                if isinstance(exc, (RetrievalConfigurationError, RetrievalIndexUnavailableError))
                else RetrievalChannelStatus.FAILED
            )
            return [], RetrievalChannelDiagnostic(
                channel=RetrievalChannel.DENSE,
                status=status,
                latency_ms=(perf_counter() - started) * 1000,
                error_code=str(exc.details.get("reason_code") or exc.code),
            )
        return candidates, RetrievalChannelDiagnostic(
            channel=RetrievalChannel.DENSE,
            status=RetrievalChannelStatus.SUCCEEDED,
            latency_ms=(perf_counter() - started) * 1000,
            candidate_count=len(candidates),
        )

    @staticmethod
    def _run_channel(
        channel: RetrievalChannel,
        operation: Any,
    ) -> tuple[list[RecallCandidate], RetrievalChannelDiagnostic]:
        started = perf_counter()
        candidates = operation()
        return candidates, RetrievalChannelDiagnostic(
            channel=channel,
            status=RetrievalChannelStatus.SUCCEEDED,
            latency_ms=(perf_counter() - started) * 1000,
            candidate_count=len(candidates),
        )

    @staticmethod
    def _skipped_diagnostics(*channels: RetrievalChannel) -> list[RetrievalChannelDiagnostic]:
        return [_skipped_diagnostic(channel) for channel in channels]


def reciprocal_rank_fusion(
    candidates_by_channel: dict[RetrievalChannel, list[RecallCandidate]],
    *,
    rrf_k: int,
    limit: int,
) -> list[RetrievalHit]:
    """按资源折叠单元命中，保留各通道原始分数、排名和最佳解释。"""

    if rrf_k <= 0 or limit <= 0:
        raise ValueError("RRF 的 rrf_k 和 limit 必须大于 0")
    states: dict[str, dict[str, Any]] = {}
    score_fields = {
        RetrievalChannel.EXACT: "exact",
        RetrievalChannel.ALIAS: "alias",
        RetrievalChannel.LEXICAL: "lexical",
        RetrievalChannel.DENSE: "dense",
    }
    for channel, channel_candidates in candidates_by_channel.items():
        seen_resources: set[str] = set()
        rank = 0
        for candidate in channel_candidates:
            resource_id = candidate.hit.resource_id
            if resource_id in seen_resources:
                continue
            seen_resources.add(resource_id)
            rank += 1
            state = states.setdefault(
                resource_id,
                {
                    "hit": candidate.hit,
                    "rrf": 0.0,
                    "scores": {},
                    "ranks": {},
                },
            )
            state["rrf"] += 1.0 / (rrf_k + rank)
            state["ranks"][channel] = rank
            score_field = score_fields.get(channel)
            if score_field:
                state["scores"][score_field] = candidate.score

    results: list[RetrievalHit] = []
    for state in states.values():
        scores = RetrievalScores(**state["scores"], final=state["rrf"])
        results.append(
            state["hit"].model_copy(
                update={
                    "scores": scores,
                    "ranks_by_channel": state["ranks"],
                }
            )
        )
    return sorted(
        results,
        key=lambda hit: (
            -(hit.scores.final or 0),
            hit.ranks_by_channel.get(RetrievalChannel.EXACT, 10**9),
            hit.ranks_by_channel.get(RetrievalChannel.ALIAS, 10**9),
            hit.resource_id,
        ),
    )[:limit]


def _candidate_from_row(row: dict[str, Any], channel: RetrievalChannel) -> RecallCandidate:
    resource_type = RetrievalResourceType(str(row["resource_type"]))
    metadata = dict(row.get("resource_metadata") or {})
    asset_id = metadata.get("asset_id")
    model_id = metadata.get("model_id")
    asset_ref = None
    if isinstance(asset_id, int) and asset_id > 0:
        asset_ref = AssetReference(
            asset_type=resource_type,
            asset_id=asset_id,
            model_id=model_id if isinstance(model_id, int) and model_id > 0 else None,
        )
    score = float(row["score"])
    return RecallCandidate(
        channel=channel,
        score=score,
        hit=RetrievalHit(
            resource_id=str(row["resource_id"]),
            resource_type=resource_type,
            source_type=RetrievalSourceType(str(row["source_type"])),
            source_id=str(row["source_id"]),
            source_resource_id=str(row["source_resource_id"]),
            unit_id=str(row["unit_id"]),
            content_kind=str(row["content_kind"]),
            title=str(row["resource_title"]),
            snippet=str(row.get("content") or "")[:240],
            matched_field=str(row.get("matched_field") or "" ) or None,
            matched_text=str(row.get("matched_text") or "") or None,
            metadata=metadata,
            provenance={
                "index_generation": row["index_generation"],
                "namespace": row["namespace"],
                "dataset_id": row.get("dataset_id"),
                "knowledge_base_id": row.get("knowledge_base_id"),
                "permission_version": row.get("permission_version"),
            },
            source_version=str(row["source_version"]),
            asset_ref=asset_ref,
        ),
    )


def _resource_types_for_purpose(purpose: RetrievalPurpose) -> tuple[RetrievalResourceType, ...]:
    mapping = {
        RetrievalPurpose.METRIC: (RetrievalResourceType.METRIC,),
        RetrievalPurpose.DIMENSION: (RetrievalResourceType.DIMENSION,),
        RetrievalPurpose.VALUE: (RetrievalResourceType.VALUE,),
        RetrievalPurpose.TERM: (RetrievalResourceType.TERM,),
    }
    try:
        return mapping[purpose]
    except KeyError as exc:
        raise ValueError(f"SEMANTIC_BINDING_PURPOSE_UNSUPPORTED:{purpose.value}") from exc


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _skipped_diagnostic(channel: RetrievalChannel) -> RetrievalChannelDiagnostic:
    return RetrievalChannelDiagnostic(
        channel=channel,
        status=RetrievalChannelStatus.SKIPPED,
    )


_SCOPED_CTE = """
WITH scoped AS (
    SELECT
        s.id AS source_id,
        s.source_type,
        g.generation AS index_generation,
        r.id AS resource_id,
        r.source_resource_id,
        r.namespace,
        r.resource_type,
        r.dataset_id,
        r.knowledge_base_id,
        r.title AS resource_title,
        r.metadata AS resource_metadata,
        r.permission_version,
        r.source_version,
        u.id AS unit_id,
        u.content_kind,
        u.title AS unit_title,
        u.content,
        u.contextual_text,
        u.metadata AS unit_metadata,
        (u.title || ' ' || u.content || ' ' || u.contextual_text) AS unit_search_document,
        (r.title || ' ' || u.title || ' ' || u.content || ' ' || u.contextual_text) AS search_document
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
    JOIN retrieval_unit AS u
      ON u.tenant_id = r.tenant_id
     AND u.resource_id = r.id
     AND u.index_generation = s.active_generation
     AND u.status = 'active'
    WHERE s.tenant_id = :tenant_id
      AND s.status = 'active'
      AND s.source_type = 'headless'
      AND r.resource_type = ANY(CAST(:resource_types AS text[]))
      AND (:dataset_scope = false OR r.dataset_id = ANY(CAST(:dataset_ids AS bigint[])))
      AND (:knowledge_scope = false OR r.knowledge_base_id = ANY(CAST(:knowledge_base_ids AS bigint[])))
      AND (:source_scope = false OR s.source_key = ANY(CAST(:source_ids AS text[])))
      AND (
          CAST(:permission_version AS text) IS NULL
          OR r.permission_version = CAST(:permission_version AS text)
      )
      AND (
          r.visibility IN ('public', 'tenant')
          OR (
              r.visibility = 'private'
              AND (
                  EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(
                          CASE WHEN jsonb_typeof(r.acl -> 'actor_ids') = 'array'
                               THEN r.acl -> 'actor_ids' ELSE '[]'::jsonb END
                      ) AS actor(value)
                      WHERE actor.value = CAST(:actor_id AS text)
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(
                          CASE WHEN jsonb_typeof(r.acl -> 'roles') = 'array'
                               THEN r.acl -> 'roles' ELSE '[]'::jsonb END
                      ) AS role(value)
                      WHERE role.value = ANY(CAST(:principal_roles AS text[]))
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(
                          CASE WHEN jsonb_typeof(r.acl -> 'role_ids') = 'array'
                               THEN r.acl -> 'role_ids' ELSE '[]'::jsonb END
                      ) AS role_id(value)
                      WHERE role_id.value = ANY(CAST(:principal_role_ids AS text[]))
                  )
              )
          )
      )
)
"""


_EXACT_SQL = text(
    _SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*, 1.0::double precision AS score, 'name'::text AS matched_field,
           resource_title AS matched_text,
           CASE WHEN content_kind = 'identity' THEN 0 ELSE 1 END AS unit_priority
    FROM scoped
    WHERE lower(btrim(resource_title)) = :normalized_query
), collapsed AS (
    SELECT DISTINCT ON (resource_id) *
    FROM ranked
    ORDER BY resource_id, unit_priority, unit_id
)
SELECT * FROM collapsed ORDER BY resource_id LIMIT :limit
"""
)


_ALIAS_SQL = text(
    _SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*, 1.0::double precision AS score, 'alias'::text AS matched_field,
           alias.value AS matched_text,
           CASE WHEN content_kind = 'identity' THEN 0 ELSE 1 END AS unit_priority
    FROM scoped
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(unit_metadata -> 'aliases') = 'array'
             THEN unit_metadata -> 'aliases' ELSE '[]'::jsonb END
    ) AS alias(value)
    WHERE lower(btrim(alias.value)) = :normalized_query
), collapsed AS (
    SELECT DISTINCT ON (resource_id) *
    FROM ranked
    ORDER BY resource_id, unit_priority, unit_id
)
SELECT * FROM collapsed ORDER BY resource_id LIMIT :limit
"""
)


_LEXICAL_SQL = text(
    _SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*,
           GREATEST(
               similarity(resource_title, :query_text),
               similarity(unit_search_document, :query_text),
               CASE WHEN position(:normalized_query IN lower(search_document)) > 0 THEN 0.99 ELSE 0 END
           )::double precision AS score,
           'text'::text AS matched_field,
           CAST(:query_text AS text) AS matched_text
    FROM scoped
    WHERE position(:normalized_query IN lower(search_document)) > 0
       OR (
           (resource_title % :query_text OR unit_search_document % :query_text)
           AND GREATEST(
               similarity(resource_title, :query_text),
               similarity(unit_search_document, :query_text)
           ) >= :lexical_threshold
       )
), collapsed AS (
    SELECT DISTINCT ON (resource_id) *
    FROM ranked
    ORDER BY resource_id, score DESC, unit_id
)
SELECT * FROM collapsed ORDER BY score DESC, resource_id LIMIT :limit
"""
)


_DENSE_SQL = text(
    _SCOPED_CTE
    + """
, ranked AS (
    SELECT scoped.*,
           (1 - (e.embedding <=> :query_vector))::double precision AS score,
           'embedding_text'::text AS matched_field,
           CAST(:query_text AS text) AS matched_text
    FROM scoped
    JOIN retrieval_embedding AS e
      ON e.tenant_id = :tenant_id
     AND e.unit_id = scoped.unit_id
     AND e.index_generation = scoped.index_generation
     AND e.embedding_profile = :embedding_profile
     AND e.status = 'active'
     AND e.embedding IS NOT NULL
), collapsed AS (
    SELECT DISTINCT ON (resource_id) *
    FROM ranked
    ORDER BY resource_id, score DESC, unit_id
)
SELECT * FROM collapsed ORDER BY score DESC, resource_id LIMIT :limit
"""
)


__all__ = [
    "HybridRecallResult",
    "HybridRetrievalConfig",
    "QueryEmbeddingProvider",
    "RecallCandidate",
    "SemanticBindingRecallStore",
    "SemanticBindingHybridRetriever",
    "SemanticBindingSearchStore",
    "SubQueryRecallResult",
    "reciprocal_rank_fusion",
]
