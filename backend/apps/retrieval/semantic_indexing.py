"""Semantic 数据集接入统一索引 generation 的来源适配器。"""

from __future__ import annotations

from sqlmodel import Session, select

from apps.retrieval.indexing import (
    IndexEmbeddingProfile,
    RetrievalIndexingService,
)
from apps.retrieval.models import RetrievalSourceModel
from apps.retrieval.semantic_projector import (
    SemanticProjectionPolicy,
    SemanticSourceProjector,
)
from apps.semantic.models.dto import (
    DatasetIndexEnqueueResult,
    DatasetIndexVersion,
    DatasetSchema,
)
from common.core.config import settings


def build_semantic_index_profile() -> IndexEmbeddingProfile:
    """统一构造 Semantic 索引与 worker 共用的物理 embedding profile。"""

    return IndexEmbeddingProfile(
        name="bge-m3-1024",
        provider=settings.RETRIEVAL_EMBEDDING_PROVIDER,
        model=settings.RETRIEVAL_EMBEDDING_MODEL,
        dimension=settings.RETRIEVAL_EMBEDDING_DIMENSION,
    )


class SemanticIndexCoordinator:
    """把 Semantic Schema 投影和通用 IndexingService 连接在一个事务内。"""

    def __init__(
        self,
        session: Session,
        profile: IndexEmbeddingProfile | None = None,
    ) -> None:
        self._session = session
        self._profile = profile or build_semantic_index_profile()

    def enqueue_dataset_rebuild(
        self,
        *,
        tenant_id: int,
        version: DatasetIndexVersion,
        schema: DatasetSchema,
    ) -> DatasetIndexEnqueueResult:
        """调用方负责 commit，使 Semantic 变更、旧索引和新 job 原子提交。"""

        dataset_id = version.dataset_id
        if dataset_id <= 0:
            raise ValueError("SEMANTIC_DATASET_NOT_PERSISTED")
        if schema.data_set.id != dataset_id:
            raise ValueError("SEMANTIC_DATASET_SCHEMA_MISMATCH")
        source_key = f"dataset:{dataset_id}"
        # 索引命名空间暂时沿用旧值，兼容已经构建的检索索引。
        namespace = f"headless:dataset:{dataset_id}"
        source = self._session.exec(
            select(RetrievalSourceModel)
            .where(
                RetrievalSourceModel.tenant_id == tenant_id,
                RetrievalSourceModel.source_type == "headless",
                RetrievalSourceModel.source_key == source_key,
            )
            .with_for_update()
        ).one_or_none()
        source_version = (
            f"schema:{version.schema_version}:index:{version.index_version}"
        )
        if source is None:
            source = RetrievalSourceModel(
                tenant_id=tenant_id,
                source_type="headless",
                source_key=source_key,
                namespace=namespace,
                source_config={
                    "dataset_id": dataset_id,
                    "embedding_profile": self._profile.name,
                    "configured_value_dimension_ids": [],
                    "max_configured_values_per_dimension": 200,
                },
                acl_policy={},
                source_version=source_version,
                status="active",
            )
            self._session.add(source)
            self._session.flush()
        if source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_PERSISTED")

        configured_dimension_ids = source.source_config.get("configured_value_dimension_ids") or []
        if not isinstance(configured_dimension_ids, list) or any(
            not isinstance(value, int) for value in configured_dimension_ids
        ):
            raise ValueError("RETRIEVAL_VALUE_DIMENSION_CONFIG_INVALID")
        max_values = source.source_config.get("max_configured_values_per_dimension", 200)
        if not isinstance(max_values, int):
            raise ValueError("RETRIEVAL_VALUE_CARDINALITY_CONFIG_INVALID")

        projected = SemanticSourceProjector(
            SemanticProjectionPolicy(
                configured_value_dimension_ids=frozenset(configured_dimension_ids),
                max_configured_values_per_dimension=max_values,
            )
        ).project(
            schema,
            tenant_id=tenant_id,
            namespace=namespace,
            source_version=source_version,
            acl=source.acl_policy,
            visibility="tenant",
        )
        generation_value = f"dataset-{dataset_id}-index-{version.index_version}"
        generation = RetrievalIndexingService(
            self._session,
            self._profile,
        ).enqueue_generation(
            source_id=source.id,
            target_generation=generation_value,
            upserts=projected,
            source_version=source_version,
            full_rebuild=True,
        )
        return DatasetIndexEnqueueResult(
            source_id=source.id,
            generation=generation.generation,
            job_ids=tuple(generation.job_ids),
        )


__all__ = [
    "SemanticIndexCoordinator",
    "build_semantic_index_profile",
]
