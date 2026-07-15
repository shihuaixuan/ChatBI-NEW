"""统一检索索引任务、完整 generation 构建与原子切换。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import or_
from sqlmodel import Session, col, select

from apps.retrieval.models import (
    RetrievalEmbeddingModel,
    RetrievalIndexGenerationModel,
    RetrievalIndexJobModel,
    RetrievalResourceModel,
    RetrievalSourceModel,
    RetrievalUnitModel,
)
from apps.retrieval.projection import ProjectedResource
from apps.retrieval.schemas import RetrievalResourceType


class BatchEmbeddingProvider(Protocol):
    """Indexer 只接受真正的批量 embedding 端口。"""

    provider: str
    model: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回等长向量列表。"""


@dataclass(frozen=True, slots=True)
class IndexEmbeddingProfile:
    """一个物理向量 profile 的不可变运行快照。"""

    name: str
    provider: str
    model: str
    dimension: int = 1024
    batch_size: int = 64

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.provider.strip() or not self.model.strip():
            raise ValueError("embedding profile 的名称、provider 和 model 不能为空")
        if self.dimension != 1024:
            raise ValueError("当前 retrieval_embedding 物理 profile 固定为 1024 维")
        if self.batch_size <= 0:
            raise ValueError("embedding batch_size 必须大于 0")


@dataclass(frozen=True, slots=True)
class DeletedResourceRef:
    """增量删除使用来源稳定键，不把数据库自增 ID 暴露给来源适配器。"""

    resource_type: RetrievalResourceType
    source_resource_id: str

    def __post_init__(self) -> None:
        if not self.source_resource_id.strip():
            raise ValueError("删除资源的 source_resource_id 不能为空")


@dataclass(frozen=True, slots=True)
class GenerationEnqueueResult:
    generation_id: int
    generation: str
    job_ids: tuple[int, ...]
    idempotent: bool


@dataclass(frozen=True, slots=True)
class IndexJobRunResult:
    job_id: int
    status: str
    attempts: int
    embedded_count: int
    reused_count: int
    activated: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    source_id: int
    active_generation: str | None
    active_unit_count: int
    active_embedding_count: int
    issues: tuple[str, ...]

    @property
    def requires_rebuild(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True, slots=True)
class IndexQueueStats:
    source_id: int
    pending: int
    running: int
    succeeded: int
    failed: int
    oldest_pending_at: datetime | None


class RetrievalIndexingError(RuntimeError):
    """已分类且可以安全记录到 job 的索引错误。"""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RetryableIndexingError(RetrievalIndexingError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, retryable=True)


class PermanentIndexingError(RetrievalIndexingError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, retryable=False)


class RetrievalIndexingService:
    """检索域唯一的索引写入、任务处理和 generation 切换入口。"""

    def __init__(
        self,
        session: Session,
        profile: IndexEmbeddingProfile,
        *,
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts 必须大于 0")
        if retry_delay.total_seconds() < 0:
            raise ValueError("retry_delay 不能为负数")
        self._session = session
        self._profile = profile
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._clock = clock or (lambda: datetime.now(UTC))

    def enqueue_generation(
        self,
        *,
        source_id: int,
        target_generation: str,
        upserts: Sequence[ProjectedResource] = (),
        deletes: Sequence[DeletedResourceRef] = (),
        source_version: str | None = None,
        full_rebuild: bool = False,
    ) -> GenerationEnqueueResult:
        """在调用方事务内持久化完整快照和 durable job，不调用外部 provider。"""

        target_generation = target_generation.strip()
        if not target_generation or len(target_generation) > 64:
            raise ValueError("target_generation 长度必须在 1 到 64 之间")
        source = self._session.exec(
            select(RetrievalSourceModel)
            .where(RetrievalSourceModel.id == source_id)
            .with_for_update()
        ).one_or_none()
        if source is None or source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_FOUND")

        resolved_source_version = self._validate_projection_scope(
            source,
            upserts,
            source_version,
        )
        existing_generation = self._generation(source.id, target_generation)
        if existing_generation is not None:
            if existing_generation.source_version != resolved_source_version:
                raise ValueError("同一 generation 不允许对应不同 source_version")
            if existing_generation.embedding_profile != self._profile.name:
                raise ValueError("同一 generation 不允许对应不同 embedding profile")
            existing_jobs = self._jobs(source.id, target_generation)
            return GenerationEnqueueResult(
                generation_id=_required_id(existing_generation.id, "generation"),
                generation=target_generation,
                job_ids=tuple(_required_id(job.id, "job") for job in existing_jobs),
                idempotent=True,
            )

        building = self._session.exec(
            select(RetrievalIndexGenerationModel).where(
                RetrievalIndexGenerationModel.source_id == source.id,
                col(RetrievalIndexGenerationModel.status).in_(["building", "ready"]),
            )
        ).first()
        if building is not None:
            raise ValueError(f"INDEX_GENERATION_ALREADY_BUILDING:{building.generation}")

        projected_keys = {(item.resource_type.value, item.source_resource_id) for item in upserts}
        if len(projected_keys) != len(upserts):
            raise ValueError("同一 generation 不允许重复投影同一个资源")
        delete_keys = {(item.resource_type.value, item.source_resource_id) for item in deletes}
        if projected_keys & delete_keys:
            raise ValueError("同一 generation 不能同时更新和删除同一个资源")

        existing_resources = list(
            self._session.exec(
                select(RetrievalResourceModel).where(RetrievalResourceModel.source_id == source.id)
            ).all()
        )
        existing_by_key = {
            (resource.resource_type, resource.source_resource_id): resource
            for resource in existing_resources
        }
        if full_rebuild:
            delete_keys |= {
                key
                for key, resource in existing_by_key.items()
                if resource.status == "active" and key not in projected_keys
            }

        now = self._clock()
        generation = RetrievalIndexGenerationModel(
            tenant_id=source.tenant_id,
            source_id=source.id,
            generation=target_generation,
            source_version=resolved_source_version,
            previous_generation=source.active_generation,
            embedding_profile=self._profile.name,
            embedding_provider=self._profile.provider,
            embedding_model=self._profile.model,
            embedding_dimension=self._profile.dimension,
            status="building",
            created_at=now,
        )
        self._session.add(generation)
        self._session.flush()

        upsert_resources: list[tuple[ProjectedResource, RetrievalResourceModel]] = []
        for projected in upserts:
            key = (projected.resource_type.value, projected.source_resource_id)
            resource = existing_by_key.get(key)
            if resource is None:
                resource = RetrievalResourceModel(
                    tenant_id=source.tenant_id,
                    namespace=projected.namespace,
                    resource_type=projected.resource_type.value,
                    source_id=source.id,
                    source_resource_id=projected.source_resource_id,
                    dataset_id=projected.dataset_id,
                    knowledge_base_id=projected.knowledge_base_id,
                    title=projected.title,
                    resource_metadata=projected.metadata,
                    acl=projected.acl,
                    visibility=projected.visibility,
                    permission_version=projected.permission_version,
                    source_version=projected.source_version,
                    content_hash=projected.content_hash,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(resource)
                self._session.flush()
                existing_by_key[key] = resource
            else:
                resource.namespace = projected.namespace
                resource.dataset_id = projected.dataset_id
                resource.knowledge_base_id = projected.knowledge_base_id
                resource.title = projected.title
                resource.resource_metadata = projected.metadata
                resource.acl = projected.acl
                resource.visibility = projected.visibility
                resource.permission_version = projected.permission_version
                resource.source_version = projected.source_version
                resource.content_hash = projected.content_hash
                resource.status = "active"
                resource.updated_at = now
            upsert_resources.append((projected, resource))

        deleted_resources: list[RetrievalResourceModel] = []
        for key in sorted(delete_keys):
            resource = existing_by_key.get(key)
            if resource is None:
                continue
            resource.status = "deleted"
            resource.updated_at = now
            deleted_resources.append(resource)

        touched_resource_ids = {
            _required_id(resource.id, "resource")
            for _, resource in upsert_resources
        } | {
            _required_id(resource.id, "resource")
            for resource in deleted_resources
        }
        self._clone_active_snapshot(
            source=source,
            target_generation=target_generation,
            excluded_resource_ids=touched_resource_ids,
            now=now,
        )
        for projected, resource in upsert_resources:
            self._stage_projected_resource(
                source=source,
                resource=resource,
                projected=projected,
                target_generation=target_generation,
                now=now,
            )

        staged_jobs: list[RetrievalIndexJobModel] = []
        operation = "rebuild" if full_rebuild else "upsert"
        for _, resource in upsert_resources:
            staged_jobs.append(
                self._new_job(source, resource, operation, target_generation, now)
            )
        for resource in deleted_resources:
            staged_jobs.append(self._new_job(source, resource, "delete", target_generation, now))
        if not staged_jobs:
            staged_jobs.append(self._new_job(source, None, "rebuild", target_generation, now))
        self._session.flush()

        generation.expected_jobs = len(staged_jobs)
        source.status = "active" if source.active_generation else "rebuilding"
        source.updated_at = now
        self._session.flush()
        return GenerationEnqueueResult(
            generation_id=_required_id(generation.id, "generation"),
            generation=target_generation,
            job_ids=tuple(_required_id(job.id, "job") for job in staged_jobs),
            idempotent=False,
        )

    def process_job(
        self,
        job_id: int,
        provider: BatchEmbeddingProvider,
    ) -> IndexJobRunResult:
        """处理单个 job；已知错误写入状态，未知异常直接向上抛出。"""

        job = self._session.exec(
            select(RetrievalIndexJobModel)
            .where(RetrievalIndexJobModel.id == job_id)
            .with_for_update()
        ).one_or_none()
        if job is None or job.id is None:
            raise ValueError("RETRIEVAL_INDEX_JOB_NOT_FOUND")
        if job.status == "succeeded":
            source = self._session.get(RetrievalSourceModel, job.source_id)
            return IndexJobRunResult(
                job_id=job.id,
                status=job.status,
                attempts=job.attempts,
                embedded_count=0,
                reused_count=0,
                activated=source is not None and source.active_generation == job.target_generation,
            )
        if job.status != "pending":
            raise ValueError(f"RETRIEVAL_INDEX_JOB_NOT_PENDING:{job.status}")
        generation = self._generation(job.source_id, job.target_generation)
        if generation is None:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_NOT_FOUND")
        if generation.embedding_profile != self._profile.name:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_PROFILE_MISMATCH")

        now = self._clock()
        if job.available_at is not None and job.available_at > now:
            raise ValueError("RETRIEVAL_INDEX_JOB_NOT_AVAILABLE")
        self._validate_provider(provider)
        job.status = "running"
        job.attempts += 1
        job.started_at = now
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        self._session.flush()

        try:
            embeddings, unit_by_id = self._pending_embeddings_for_job(job)
            needs_embedding = [embedding for embedding in embeddings if embedding.embedding is None]
            reused_count = len(embeddings) - len(needs_embedding)
            for offset in range(0, len(needs_embedding), self._profile.batch_size):
                batch = needs_embedding[offset : offset + self._profile.batch_size]
                texts = [_unit_embedding_text(unit_by_id[embedding.unit_id]) for embedding in batch]
                vectors = self._embed_batch(provider, texts)
                self._validate_vectors(vectors, len(batch))
                for embedding, vector in zip(batch, vectors, strict=True):
                    embedding.embedding = vector
                    embedding.error_code = None
                    embedding.error_message = None

            job.status = "succeeded"
            job.finished_at = self._clock()
            job.updated_at = job.finished_at
            job.available_at = None
            self._session.flush()
            activated = self._activate_if_ready(job.source_id, job.target_generation)
            return IndexJobRunResult(
                job_id=job.id,
                status=job.status,
                attempts=job.attempts,
                embedded_count=len(needs_embedding),
                reused_count=reused_count,
                activated=activated,
            )
        except RetrievalIndexingError as exc:
            return self._failure_result(job, exc)

    def process_next(
        self,
        provider: BatchEmbeddingProvider,
    ) -> IndexJobRunResult | None:
        """使用 SKIP LOCKED 领取一个可用 job，供多 worker 并发消费。"""

        now = self._clock()
        job = self._session.exec(
            select(RetrievalIndexJobModel)
            .join(
                RetrievalIndexGenerationModel,
                (col(RetrievalIndexGenerationModel.tenant_id) == col(RetrievalIndexJobModel.tenant_id))
                & (col(RetrievalIndexGenerationModel.source_id) == col(RetrievalIndexJobModel.source_id))
                & (
                    col(RetrievalIndexGenerationModel.generation)
                    == col(RetrievalIndexJobModel.target_generation)
                ),
            )
            .where(
                RetrievalIndexJobModel.status == "pending",
                RetrievalIndexGenerationModel.status == "building",
                RetrievalIndexGenerationModel.embedding_profile == self._profile.name,
                or_(
                    col(RetrievalIndexJobModel.available_at).is_(None),
                    col(RetrievalIndexJobModel.available_at) <= now,
                ),
            )
            .order_by(col(RetrievalIndexJobModel.available_at), col(RetrievalIndexJobModel.id))
            .with_for_update(skip_locked=True, of=RetrievalIndexJobModel)
        ).first()
        if job is None or job.id is None:
            return None
        return self.process_job(job.id, provider)

    def retry_generation(self, source_id: int, generation: str) -> tuple[int, ...]:
        """显式重试终态失败 generation，不对永久错误做静默 fallback。"""

        model = self._generation(source_id, generation)
        if model is None:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_NOT_FOUND")
        if model.status != "failed":
            raise ValueError(f"RETRIEVAL_INDEX_GENERATION_NOT_FAILED:{model.status}")
        jobs = self._jobs(source_id, generation)
        failed_jobs = [job for job in jobs if job.status == "failed"]
        if not failed_jobs:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_HAS_NO_FAILED_JOB")
        now = self._clock()
        for job in failed_jobs:
            job.status = "pending"
            job.attempts = 0
            job.available_at = now
            job.started_at = None
            job.finished_at = None
            job.error_code = None
            job.error_message = None
        model.status = "building"
        model.failed_jobs = 0
        model.error_code = None
        model.error_message = None
        model.finished_at = None
        source = self._session.get(RetrievalSourceModel, source_id)
        if source is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_FOUND")
        source.status = "active" if source.active_generation else "rebuilding"
        source.updated_at = now
        self._session.flush()
        return tuple(_required_id(job.id, "job") for job in failed_jobs)

    def rollback_generation(self, source_id: int, generation: str) -> None:
        """在一个事务内把保留的完整 generation 重新激活。"""

        source = self._session.exec(
            select(RetrievalSourceModel)
            .where(RetrievalSourceModel.id == source_id)
            .with_for_update()
        ).one_or_none()
        if source is None or source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_FOUND")
        target = self._generation(source.id, generation)
        if target is None:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_NOT_FOUND")
        if target.status not in {"superseded", "active"}:
            raise ValueError(f"RETRIEVAL_INDEX_GENERATION_NOT_ROLLBACKABLE:{target.status}")
        if source.active_generation == generation:
            return

        target_units = self._units_for_generation(source.id, generation)
        target_embeddings = self._embeddings_for_units(target_units)
        if len(target_units) != len(target_embeddings) or any(item.embedding is None for item in target_embeddings):
            raise ValueError("RETRIEVAL_INDEX_GENERATION_INCOMPLETE")

        now = self._clock()
        current = self._generation(source.id, source.active_generation) if source.active_generation else None
        for embedding in self._active_embeddings(source.id):
            embedding.status = "superseded"
        for unit in self._active_units(source.id):
            unit.status = "superseded"
        if current is not None:
            current.status = "superseded"
            current.finished_at = now
        self._session.flush()

        for unit in target_units:
            unit.status = "active"
        for embedding in target_embeddings:
            embedding.status = "active"
        target.status = "active"
        target.activated_at = now
        target.finished_at = now

        target_resource_ids = {unit.resource_id for unit in target_units}
        target_jobs = self._jobs(source.id, generation)
        deleted_resource_ids = {
            job.resource_id
            for job in target_jobs
            if job.operation == "delete" and job.resource_id is not None
        }
        for resource in self._resources(source.id):
            resource_id = _required_id(resource.id, "resource")
            if resource_id in target_resource_ids:
                resource.status = "active"
            elif resource_id in deleted_resource_ids:
                resource.status = "deleted"
            else:
                resource.status = "inactive"
            resource.updated_at = now

        source.active_generation = generation
        source.source_version = target.source_version
        source.status = "active"
        source.updated_at = now
        self._session.flush()

    def reconcile_source(self, source_id: int) -> ReconciliationReport:
        """检查 active 指针、版本、快照完整性；修复由 full rebuild 统一完成。"""

        source = self._session.get(RetrievalSourceModel, source_id)
        if source is None or source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_FOUND")
        issues: list[str] = []
        generation = self._generation(source.id, source.active_generation) if source.active_generation else None
        if source.active_generation is None:
            issues.append("ACTIVE_GENERATION_MISSING")
        elif generation is None or generation.status != "active":
            issues.append("ACTIVE_GENERATION_POINTER_INVALID")
        elif generation.source_version != source.source_version:
            issues.append("SOURCE_VERSION_MISMATCH")

        units = self._active_units(source.id)
        embeddings = self._active_embeddings(source.id)
        if source.active_generation and any(unit.index_generation != source.active_generation for unit in units):
            issues.append("MIXED_ACTIVE_GENERATIONS")
        if len(units) != len(embeddings):
            issues.append("ACTIVE_EMBEDDING_COUNT_MISMATCH")
        if any(embedding.embedding is None for embedding in embeddings):
            issues.append("ACTIVE_EMBEDDING_MISSING_VECTOR")
        active_resource_ids = {unit.resource_id for unit in units}
        if any(
            resource.status == "active" and _required_id(resource.id, "resource") not in active_resource_ids
            for resource in self._resources(source.id)
        ):
            issues.append("ACTIVE_RESOURCE_WITHOUT_UNIT")
        return ReconciliationReport(
            source_id=source.id,
            active_generation=source.active_generation,
            active_unit_count=len(units),
            active_embedding_count=len(embeddings),
            issues=tuple(sorted(set(issues))),
        )

    def queue_stats(self, source_id: int) -> IndexQueueStats:
        jobs = list(
            self._session.exec(
                select(RetrievalIndexJobModel).where(RetrievalIndexJobModel.source_id == source_id)
            ).all()
        )
        pending_dates = [job.available_at or job.created_at for job in jobs if job.status == "pending"]
        return IndexQueueStats(
            source_id=source_id,
            pending=sum(job.status == "pending" for job in jobs),
            running=sum(job.status == "running" for job in jobs),
            succeeded=sum(job.status == "succeeded" for job in jobs),
            failed=sum(job.status == "failed" for job in jobs),
            oldest_pending_at=min((value for value in pending_dates if value is not None), default=None),
        )

    def _validate_projection_scope(
        self,
        source: RetrievalSourceModel,
        upserts: Sequence[ProjectedResource],
        source_version: str | None,
    ) -> str:
        versions = {item.source_version for item in upserts}
        if source_version is not None:
            versions.add(source_version)
        if len(versions) > 1:
            raise ValueError("同一 generation 的 source_version 必须一致")
        resolved = next(iter(versions), source.source_version)
        if not resolved.strip():
            raise ValueError("source_version 不能为空")
        for item in upserts:
            if item.tenant_id != source.tenant_id:
                raise ValueError("投影资源与检索来源 tenant 不一致")
            if item.namespace != source.namespace:
                raise ValueError("投影资源与检索来源 namespace 不一致")
            if item.source_type.value != source.source_type:
                raise ValueError("投影资源与检索来源 source_type 不一致")
        return resolved

    def _clone_active_snapshot(
        self,
        *,
        source: RetrievalSourceModel,
        target_generation: str,
        excluded_resource_ids: set[int],
        now: datetime,
    ) -> None:
        if source.active_generation is None:
            return
        old_units = self._active_units(source.id or 0)
        old_embeddings = {
            embedding.unit_id: embedding
            for embedding in self._embeddings_for_units(old_units)
            if embedding.embedding_profile == self._profile.name
        }
        active_resource_ids = {
            _required_id(resource.id, "resource")
            for resource in self._resources(source.id or 0)
            if resource.status == "active"
        }
        for old_unit in old_units:
            if old_unit.resource_id in excluded_resource_ids or old_unit.resource_id not in active_resource_ids:
                continue
            new_unit = self._copy_unit(old_unit, target_generation, now)
            self._session.add(new_unit)
            self._session.flush()
            old_embedding = old_embeddings.get(_required_id(old_unit.id, "unit"))
            self._session.add(
                self._copy_or_pending_embedding(
                    source.tenant_id,
                    _required_id(new_unit.id, "unit"),
                    target_generation,
                    _unit_embedding_text(old_unit),
                    old_embedding,
                    now,
                )
            )

    def _stage_projected_resource(
        self,
        *,
        source: RetrievalSourceModel,
        resource: RetrievalResourceModel,
        projected: ProjectedResource,
        target_generation: str,
        now: datetime,
    ) -> None:
        resource_id = _required_id(resource.id, "resource")
        old_units = {
            unit.unit_key: unit
            for unit in self._session.exec(
                select(RetrievalUnitModel).where(
                    RetrievalUnitModel.resource_id == resource_id,
                    RetrievalUnitModel.status == "active",
                )
            ).all()
        }
        old_embeddings = {
            embedding.unit_id: embedding
            for embedding in self._embeddings_for_units(list(old_units.values()))
            if embedding.embedding_profile == self._profile.name
        }
        for projected_unit in projected.units:
            unit = RetrievalUnitModel(
                tenant_id=source.tenant_id,
                resource_id=resource_id,
                unit_key=projected_unit.unit_key,
                content_kind=projected_unit.content_kind,
                title=projected_unit.title,
                content=projected_unit.content,
                contextual_text=projected_unit.contextual_text,
                language=projected_unit.language,
                unit_metadata=projected_unit.metadata,
                content_hash=projected_unit.content_hash,
                index_generation=target_generation,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            self._session.add(unit)
            self._session.flush()
            old_unit = old_units.get(projected_unit.unit_key)
            old_embedding = (
                old_embeddings.get(_required_id(old_unit.id, "unit"))
                if old_unit is not None
                else None
            )
            reusable_embedding = (
                old_embedding
                if old_embedding is not None
                and old_embedding.text_hash == projected_unit.embedding_text_hash
                else None
            )
            self._session.add(
                self._new_embedding(
                    tenant_id=source.tenant_id,
                    unit_id=_required_id(unit.id, "unit"),
                    generation=target_generation,
                    text_hash=projected_unit.embedding_text_hash,
                    old_embedding=reusable_embedding,
                    now=now,
                )
            )

    def _new_job(
        self,
        source: RetrievalSourceModel,
        resource: RetrievalResourceModel | None,
        operation: str,
        generation: str,
        now: datetime,
    ) -> RetrievalIndexJobModel:
        job = RetrievalIndexJobModel(
            tenant_id=source.tenant_id,
            source_id=_required_id(source.id, "source"),
            resource_id=_required_id(resource.id, "resource") if resource is not None else None,
            operation=operation,
            target_generation=generation,
            status="pending",
            attempts=0,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        self._session.add(job)
        return job

    def _pending_embeddings_for_job(
        self,
        job: RetrievalIndexJobModel,
    ) -> tuple[list[RetrievalEmbeddingModel], dict[int, RetrievalUnitModel]]:
        statement = select(RetrievalUnitModel).join(
            RetrievalResourceModel,
            col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
        ).where(
            RetrievalResourceModel.source_id == job.source_id,
            RetrievalUnitModel.index_generation == job.target_generation,
            RetrievalUnitModel.status == "pending",
        )
        if job.resource_id is not None:
            statement = statement.where(RetrievalUnitModel.resource_id == job.resource_id)
        units = list(self._session.exec(statement).all())
        unit_by_id = {_required_id(unit.id, "unit"): unit for unit in units}
        if not unit_by_id:
            return [], {}
        embeddings = list(
            self._session.exec(
                select(RetrievalEmbeddingModel).where(
                    col(RetrievalEmbeddingModel.unit_id).in_(unit_by_id),
                    RetrievalEmbeddingModel.embedding_profile == self._profile.name,
                    RetrievalEmbeddingModel.index_generation == job.target_generation,
                    RetrievalEmbeddingModel.status == "pending",
                )
            ).all()
        )
        if len(embeddings) != len(units):
            raise PermanentIndexingError(
                "INDEX_GENERATION_EMBEDDING_MISSING",
                "目标 generation 的 unit 与 embedding 数量不一致",
            )
        return embeddings, unit_by_id

    def _validate_provider(self, provider: BatchEmbeddingProvider) -> None:
        if provider.provider != self._profile.provider:
            raise ValueError("embedding provider 与 profile 不一致")
        if provider.model != self._profile.model:
            raise ValueError("embedding model 与 profile 不一致")
        if provider.dimension != self._profile.dimension:
            raise ValueError("embedding dimension 与 profile 不一致")

    def _validate_vectors(self, vectors: list[list[float]], expected_count: int) -> None:
        if len(vectors) != expected_count:
            raise PermanentIndexingError(
                "EMBEDDING_BATCH_COUNT_MISMATCH",
                f"embedding 批量结果数量错误：期望 {expected_count}，实际 {len(vectors)}",
            )
        for vector in vectors:
            if len(vector) != self._profile.dimension:
                raise PermanentIndexingError(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    f"embedding 维度错误：期望 {self._profile.dimension}，实际 {len(vector)}",
                )

    def _embed_batch(
        self,
        provider: BatchEmbeddingProvider,
        texts: list[str],
    ) -> list[list[float]]:
        try:
            return provider.embed_documents(texts)
        except httpx.TimeoutException as exc:
            raise RetryableIndexingError(
                "EMBEDDING_PROVIDER_TIMEOUT",
                "批量 embedding 请求超时",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error_type = (
                RetryableIndexingError
                if status_code in {408, 429} or status_code >= 500
                else PermanentIndexingError
            )
            raise error_type(
                f"EMBEDDING_PROVIDER_HTTP_{status_code}",
                f"批量 embedding 请求返回 HTTP {status_code}",
            ) from exc
        except httpx.TransportError as exc:
            raise RetryableIndexingError(
                "EMBEDDING_PROVIDER_TRANSPORT_ERROR",
                "批量 embedding 网络传输失败",
            ) from exc
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise PermanentIndexingError(
                "EMBEDDING_PROVIDER_INVALID_RESPONSE",
                "批量 embedding 返回结构无效",
            ) from exc

    def _record_known_failure(
        self,
        job: RetrievalIndexJobModel,
        error: RetrievalIndexingError,
    ) -> None:
        now = self._clock()
        job.updated_at = now
        job.error_code = error.code
        job.error_message = str(error)
        if error.retryable and job.attempts < self._max_attempts:
            job.status = "pending"
            job.available_at = now + self._retry_delay
            job.finished_at = None
        else:
            job.status = "failed"
            job.available_at = None
            job.finished_at = now
            generation = self._generation(job.source_id, job.target_generation)
            if generation is not None:
                generation.status = "failed"
                generation.error_code = error.code
                generation.error_message = str(error)
                generation.finished_at = now
            source = self._session.get(RetrievalSourceModel, job.source_id)
            if source is not None:
                source.status = "active" if source.active_generation else "failed"
                source.updated_at = now
        self._refresh_generation_job_counts(job.source_id, job.target_generation)
        self._session.flush()

    def _failure_result(
        self,
        job: RetrievalIndexJobModel,
        error: RetrievalIndexingError,
    ) -> IndexJobRunResult:
        self._record_known_failure(job, error)
        return IndexJobRunResult(
            job_id=_required_id(job.id, "job"),
            status=job.status,
            attempts=job.attempts,
            embedded_count=0,
            reused_count=0,
            activated=False,
            error_code=error.code,
        )

    def _activate_if_ready(self, source_id: int, generation_value: str) -> bool:
        # 多个任务可能同时完成；锁住 generation 后重新读取任务状态，确保最后一个提交者负责激活。
        generation = self._generation(source_id, generation_value, for_update=True)
        if generation is None:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_NOT_FOUND")
        if generation.embedding_profile != self._profile.name:
            raise ValueError("RETRIEVAL_INDEX_GENERATION_PROFILE_MISMATCH")
        jobs = self._jobs(source_id, generation_value)
        self._refresh_generation_job_counts(source_id, generation_value)
        if not jobs or any(job.status != "succeeded" for job in jobs):
            return False

        source = self._session.exec(
            select(RetrievalSourceModel)
            .where(RetrievalSourceModel.id == source_id)
            .with_for_update()
        ).one_or_none()
        if source is None or source.id is None:
            raise ValueError("RETRIEVAL_SOURCE_NOT_FOUND")
        target_units = self._units_for_generation(source.id, generation_value)
        target_embeddings = self._embeddings_for_units(target_units, status="pending")
        if len(target_units) != len(target_embeddings) or any(item.embedding is None for item in target_embeddings):
            raise PermanentIndexingError(
                "INDEX_GENERATION_INCOMPLETE",
                "generation 尚未生成全部向量，禁止激活",
            )

        now = self._clock()
        generation.status = "ready"
        self._session.flush()
        for embedding in self._active_embeddings(source.id):
            embedding.status = "superseded"
        for unit in self._active_units(source.id):
            unit.status = "superseded"
        previous = self._generation(source.id, source.active_generation) if source.active_generation else None
        if previous is not None:
            previous.status = "superseded"
            previous.finished_at = now
        self._session.flush()

        for unit in target_units:
            unit.status = "active"
            unit.updated_at = now
        for embedding in target_embeddings:
            embedding.status = "active"
            embedding.updated_at = now
        generation.status = "active"
        generation.resource_count = len({unit.resource_id for unit in target_units})
        generation.unit_count = len(target_units)
        generation.embedding_count = len(target_embeddings)
        generation.activated_at = now
        generation.finished_at = now
        source.active_generation = generation_value
        source.source_version = generation.source_version
        source.status = "active"
        source.updated_at = now
        self._session.flush()
        return True

    def _refresh_generation_job_counts(self, source_id: int, generation_value: str) -> None:
        generation = self._generation(source_id, generation_value)
        if generation is None:
            return
        jobs = self._jobs(source_id, generation_value)
        generation.expected_jobs = len(jobs)
        generation.succeeded_jobs = sum(job.status == "succeeded" for job in jobs)
        generation.failed_jobs = sum(job.status == "failed" for job in jobs)

    def _generation(
        self,
        source_id: int,
        generation: str | None,
        *,
        for_update: bool = False,
    ) -> RetrievalIndexGenerationModel | None:
        if generation is None:
            return None
        statement = select(RetrievalIndexGenerationModel).where(
            RetrievalIndexGenerationModel.source_id == source_id,
            RetrievalIndexGenerationModel.generation == generation,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).one_or_none()

    def _jobs(self, source_id: int, generation: str) -> list[RetrievalIndexJobModel]:
        return list(
            self._session.exec(
                select(RetrievalIndexJobModel)
                .where(
                    RetrievalIndexJobModel.source_id == source_id,
                    RetrievalIndexJobModel.target_generation == generation,
                )
                .order_by(col(RetrievalIndexJobModel.id))
            ).all()
        )

    def _resources(self, source_id: int) -> list[RetrievalResourceModel]:
        return list(
            self._session.exec(
                select(RetrievalResourceModel).where(RetrievalResourceModel.source_id == source_id)
            ).all()
        )

    def _units_for_generation(self, source_id: int, generation: str) -> list[RetrievalUnitModel]:
        return list(
            self._session.exec(
                select(RetrievalUnitModel)
                .join(
                    RetrievalResourceModel,
                    col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
                )
                .where(
                    RetrievalResourceModel.source_id == source_id,
                    RetrievalUnitModel.index_generation == generation,
                    col(RetrievalUnitModel.status).in_(["pending", "superseded", "active"]),
                )
                .order_by(col(RetrievalUnitModel.id))
            ).all()
        )

    def _active_units(self, source_id: int) -> list[RetrievalUnitModel]:
        return list(
            self._session.exec(
                select(RetrievalUnitModel)
                .join(
                    RetrievalResourceModel,
                    col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
                )
                .where(
                    RetrievalResourceModel.source_id == source_id,
                    RetrievalUnitModel.status == "active",
                )
                .order_by(col(RetrievalUnitModel.id))
            ).all()
        )

    def _embeddings_for_units(
        self,
        units: Sequence[RetrievalUnitModel],
        *,
        status: str | None = None,
    ) -> list[RetrievalEmbeddingModel]:
        unit_ids = [_required_id(unit.id, "unit") for unit in units]
        if not unit_ids:
            return []
        statement = select(RetrievalEmbeddingModel).where(
            col(RetrievalEmbeddingModel.unit_id).in_(unit_ids),
            RetrievalEmbeddingModel.embedding_profile == self._profile.name,
        )
        if status is not None:
            statement = statement.where(RetrievalEmbeddingModel.status == status)
        return list(self._session.exec(statement.order_by(col(RetrievalEmbeddingModel.id))).all())

    def _active_embeddings(self, source_id: int) -> list[RetrievalEmbeddingModel]:
        return list(
            self._session.exec(
                select(RetrievalEmbeddingModel)
                .join(
                    RetrievalUnitModel,
                    col(RetrievalUnitModel.id) == col(RetrievalEmbeddingModel.unit_id),
                )
                .join(
                    RetrievalResourceModel,
                    col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
                )
                .where(
                    RetrievalResourceModel.source_id == source_id,
                    RetrievalEmbeddingModel.embedding_profile == self._profile.name,
                    RetrievalEmbeddingModel.status == "active",
                )
                .order_by(col(RetrievalEmbeddingModel.id))
            ).all()
        )

    def _copy_unit(
        self,
        old: RetrievalUnitModel,
        generation: str,
        now: datetime,
    ) -> RetrievalUnitModel:
        return RetrievalUnitModel(
            tenant_id=old.tenant_id,
            resource_id=old.resource_id,
            unit_key=old.unit_key,
            content_kind=old.content_kind,
            title=old.title,
            content=old.content,
            contextual_text=old.contextual_text,
            language=old.language,
            unit_metadata=old.unit_metadata,
            lexical_vector=old.lexical_vector,
            content_hash=old.content_hash,
            index_generation=generation,
            status="pending",
            created_at=now,
            updated_at=now,
        )

    def _copy_or_pending_embedding(
        self,
        tenant_id: int,
        unit_id: int,
        generation: str,
        embedding_text: str,
        old_embedding: RetrievalEmbeddingModel | None,
        now: datetime,
    ) -> RetrievalEmbeddingModel:
        text_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
        reusable = old_embedding if old_embedding is not None and old_embedding.text_hash == text_hash else None
        return self._new_embedding(
            tenant_id=tenant_id,
            unit_id=unit_id,
            generation=generation,
            text_hash=text_hash,
            old_embedding=reusable,
            now=now,
        )

    def _new_embedding(
        self,
        *,
        tenant_id: int,
        unit_id: int,
        generation: str,
        text_hash: str,
        old_embedding: RetrievalEmbeddingModel | None,
        now: datetime,
    ) -> RetrievalEmbeddingModel:
        vector = list(old_embedding.embedding) if old_embedding is not None and old_embedding.embedding is not None else None
        return RetrievalEmbeddingModel(
            tenant_id=tenant_id,
            unit_id=unit_id,
            embedding_profile=self._profile.name,
            provider=self._profile.provider,
            model=self._profile.model,
            dimension=self._profile.dimension,
            embedding=vector,
            text_hash=text_hash,
            index_generation=generation,
            status="pending",
            created_at=now,
            updated_at=now,
        )


def _required_id(value: int | None, entity: str) -> int:
    if value is None:
        raise ValueError(f"{entity} 尚未持久化")
    return value


def _unit_embedding_text(unit: RetrievalUnitModel) -> str:
    return "\n".join(
        part
        for part in [unit.title, unit.content, unit.contextual_text]
        if part
    )
__all__ = [
    "BatchEmbeddingProvider",
    "DeletedResourceRef",
    "GenerationEnqueueResult",
    "IndexEmbeddingProfile",
    "IndexJobRunResult",
    "IndexQueueStats",
    "PermanentIndexingError",
    "ReconciliationReport",
    "RetrievalIndexingError",
    "RetrievalIndexingService",
    "RetryableIndexingError",
]
