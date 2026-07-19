"""Knowledge SQL 示例接入统一索引 generation 的来源适配器。"""

from __future__ import annotations

from sqlmodel import Session, select

from apps.knowledge.models.dto import (
    SQLExampleIndexEnqueueResult,
    SQLExampleSourceSnapshot,
)
from apps.retrieval.index_profile import build_retrieval_index_profile
from apps.retrieval.indexing import IndexEmbeddingProfile, RetrievalIndexingService
from apps.retrieval.models.dto import RetrievalSourceType
from apps.retrieval.models.orm import RetrievalSourceModel
from apps.retrieval.sql_example_projector import SQLExampleSourceProjector
from apps.retrieval.worker import submit_index_jobs


class SQLExampleIndexCoordinator:
    """把 Knowledge 完整快照和 Retrieval durable job 暂存在调用方事务内。"""

    def __init__(
        self,
        session: Session,
        profile: IndexEmbeddingProfile | None = None,
    ) -> None:
        self._session = session
        self._profile = profile or build_retrieval_index_profile()

    def stage_rebuild(
        self,
        snapshot: SQLExampleSourceSnapshot,
    ) -> SQLExampleIndexEnqueueResult:
        """暂存工作空间完整 generation；调用方负责与源数据一并 commit。"""

        workspace_id = snapshot.workspace_id
        source_key = f"workspace:{workspace_id}:sql-examples"
        namespace = f"knowledge:workspace:{workspace_id}:sql-examples"
        source = self._session.exec(
            select(RetrievalSourceModel)
            .where(
                RetrievalSourceModel.tenant_id == workspace_id,
                RetrievalSourceModel.source_type
                == RetrievalSourceType.SQL_EXEMPLAR.value,
                RetrievalSourceModel.source_key == source_key,
            )
            .with_for_update()
        ).one_or_none()

        if source is None:
            revision = 1
            source = RetrievalSourceModel(
                tenant_id=workspace_id,
                source_type=RetrievalSourceType.SQL_EXEMPLAR.value,
                source_key=source_key,
                namespace=namespace,
                source_config={
                    "embedding_profile": self._profile.name,
                    "revision": revision,
                },
                acl_policy={},
                source_version=snapshot.source_version,
                status="active",
            )
            self._session.add(source)
            self._session.flush()
        else:
            stored_revision = source.source_config.get("revision", 0)
            if type(stored_revision) is not int or stored_revision < 0:
                raise ValueError("SQL_EXAMPLE_SOURCE_REVISION_INVALID")
            revision = stored_revision + 1
            source.source_config = {
                **source.source_config,
                "embedding_profile": self._profile.name,
                "revision": revision,
            }

        if source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_PERSISTED")
        projected = SQLExampleSourceProjector().project(
            snapshot,
            namespace=namespace,
            acl=source.acl_policy,
        )
        generation_value = f"sql-examples-{workspace_id}-{revision}"
        generation = RetrievalIndexingService(
            self._session,
            self._profile,
        ).enqueue_generation(
            source_id=source.id,
            target_generation=generation_value,
            upserts=projected,
            source_version=snapshot.source_version,
            full_rebuild=True,
        )
        return SQLExampleIndexEnqueueResult(
            source_id=source.id,
            generation=generation.generation,
            job_ids=generation.job_ids,
        )

    def submit(self, job_ids: tuple[int, ...]) -> None:
        """源事务提交成功后唤醒统一 worker。"""

        submit_index_jobs(job_ids)


__all__ = ["SQLExampleIndexCoordinator"]
