"""统一检索增量索引与 generation 生命周期的 PostgreSQL 集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, col, select

from apps.retrieval.indexing import (
    DeletedResourceRef,
    IndexEmbeddingProfile,
    RetrievalIndexingService,
    RetryableIndexingError,
)
from apps.retrieval.models import (
    RetrievalEmbeddingModel,
    RetrievalIndexGenerationModel,
    RetrievalResourceModel,
    RetrievalSourceModel,
    RetrievalUnitModel,
)
from apps.retrieval.projection import ProjectedResource
from apps.retrieval.schemas import RetrievalResourceType
from apps.retrieval.semantic_indexing import SemanticIndexCoordinator
from apps.retrieval.semantic_projector import SemanticSourceProjector
from apps.semantic.models.dto import DatasetIndexVersion, DatasetSchema, SchemaElement
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import SemanticSchemaService
from common.core.db import engine

TENANT_ID = 9_920_001
NAMESPACE = "headless:dataset:20"
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
PROFILE = IndexEmbeddingProfile(
    name="bge-m3-1024",
    provider="static",
    model="static-bge-m3",
    dimension=1024,
    batch_size=8,
)


class _StaticBatchProvider:
    provider = "static"
    model = "static-bge-m3"
    dimension = 1024

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index + 1)] * self.dimension for index, _ in enumerate(texts)]


class _RetryableFailureProvider(_StaticBatchProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        raise RetryableIndexingError("EMBEDDING_PROVIDER_TIMEOUT", "批量 embedding 超时")


@pytest.fixture
def session() -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as value:
            try:
                yield value
            finally:
                value.close()
                transaction.rollback()


def _source(session: Session, source_key: str) -> RetrievalSourceModel:
    source = RetrievalSourceModel(
        tenant_id=TENANT_ID,
        source_type="headless",
        source_key=source_key,
        namespace=NAMESPACE,
        source_version="schema-0",
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(source)
    session.flush()
    return source


def _projected_resources(*, source_version: str, first_aliases: list[str] | None = None) -> list[ProjectedResource]:
    dataset = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        id=20,
        name="经营分析",
        biz_name="business_analysis",
        type="DATASET",
    )
    schema = DatasetSchema(
        data_set=dataset,
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="销售额",
                biz_name="sales_amount",
                type="METRIC",
                alias=first_aliases or ["GMV"],
                description="支付成功订单金额",
                default_agg="SUM",
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=101,
                name="订单量",
                biz_name="order_count",
                type="METRIC",
                alias=["单量"],
                description="支付成功订单数量",
                default_agg="COUNT",
            ),
        ],
    )
    return SemanticSourceProjector().project(
        schema,
        tenant_id=TENANT_ID,
        namespace=NAMESPACE,
        source_version=source_version,
        acl={"roles": ["analyst"]},
        permission_version="permission-1",
    )


def _service(session: Session, *, max_attempts: int = 3) -> RetrievalIndexingService:
    return RetrievalIndexingService(
        session,
        PROFILE,
        max_attempts=max_attempts,
        retry_delay=timedelta(0),
        clock=lambda: NOW,
    )


def _run_jobs(
    service: RetrievalIndexingService,
    job_ids: tuple[int, ...],
    provider: _StaticBatchProvider,
) -> None:
    for job_id in job_ids:
        service.process_job(job_id, provider)


def _resource(
    session: Session,
    source_id: int,
    source_resource_id: str,
) -> RetrievalResourceModel:
    return session.exec(
        select(RetrievalResourceModel).where(
            RetrievalResourceModel.source_id == source_id,
            RetrievalResourceModel.source_resource_id == source_resource_id,
        )
    ).one()


def test_generation_is_idempotent_and_incremental_update_only_reembeds_changed_text(session: Session):
    source = _source(session, "indexing-idempotent")
    source_id = source.id or 0
    service = _service(session)
    provider = _StaticBatchProvider()
    initial = _projected_resources(source_version="schema-1")

    first = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-1",
        upserts=initial,
        full_rebuild=True,
    )
    duplicate = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-1",
        upserts=initial,
        full_rebuild=True,
    )
    assert duplicate.idempotent is True
    assert duplicate.job_ids == first.job_ids

    _run_jobs(service, first.job_ids, provider)
    session.refresh(source)
    assert source.active_generation == "generation-1"
    assert source.source_version == "schema-1"
    assert sum(len(call) for call in provider.calls) == 8

    changed = next(
        resource
        for resource in _projected_resources(
            source_version="schema-2",
            first_aliases=["GMV", "成交金额"],
        )
        if resource.source_resource_id == "METRIC:100"
    )
    second = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-2",
        upserts=[changed],
    )
    result = service.process_job(second.job_ids[0], provider)

    assert result.activated is True
    assert result.embedded_count == 1
    assert result.reused_count == 2
    session.refresh(source)
    assert source.active_generation == "generation-2"
    active_units = list(
        session.exec(
            select(RetrievalUnitModel)
            .join(
                RetrievalResourceModel,
                col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
            )
            .where(
                RetrievalResourceModel.source_id == source_id,
                RetrievalUnitModel.status == "active",
            )
        ).all()
    )
    assert len(active_units) == 8
    assert {unit.index_generation for unit in active_units} == {"generation-2"}
    assert service.reconcile_source(source_id).issues == ()
    assert service.queue_stats(source_id).succeeded == 4


def test_retryable_failure_keeps_old_generation_until_explicit_retry_succeeds(session: Session):
    source = _source(session, "indexing-retry")
    source_id = source.id or 0
    service = _service(session, max_attempts=2)
    good_provider = _StaticBatchProvider()
    initial = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-1",
        upserts=_projected_resources(source_version="schema-1"),
        full_rebuild=True,
    )
    _run_jobs(service, initial.job_ids, good_provider)

    changed = next(
        resource
        for resource in _projected_resources(source_version="schema-2", first_aliases=["流水"])
        if resource.source_resource_id == "METRIC:100"
    )
    second = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-2",
        upserts=[changed],
    )
    session.refresh(source)
    assert source.status == "active"
    failing_provider = _RetryableFailureProvider()
    first_failure = service.process_job(second.job_ids[0], failing_provider)
    second_failure = service.process_job(second.job_ids[0], failing_provider)

    assert first_failure.status == "pending"
    assert second_failure.status == "failed"
    assert second_failure.error_code == "EMBEDDING_PROVIDER_TIMEOUT"
    session.refresh(source)
    assert source.active_generation == "generation-1"
    assert source.status == "active"
    assert {
        unit.index_generation
        for unit in session.exec(
            select(RetrievalUnitModel)
            .join(
                RetrievalResourceModel,
                col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
            )
            .where(
                RetrievalResourceModel.source_id == source_id,
                RetrievalUnitModel.status == "active",
            )
        ).all()
    } == {"generation-1"}

    retried_job_ids = service.retry_generation(source_id, "generation-2")
    retry_result = service.process_job(retried_job_ids[0], good_provider)
    assert retry_result.activated is True
    session.refresh(source)
    assert source.active_generation == "generation-2"
    assert source.status == "active"


def test_delete_uses_tombstone_filter_and_retained_generation_can_rollback(session: Session):
    source = _source(session, "indexing-delete-rollback")
    source_id = source.id or 0
    service = _service(session)
    provider = _StaticBatchProvider()
    initial = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-1",
        upserts=_projected_resources(source_version="schema-1"),
        full_rebuild=True,
    )
    _run_jobs(service, initial.job_ids, provider)

    deleted = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-2",
        deletes=[DeletedResourceRef(RetrievalResourceType.METRIC, "METRIC:100")],
        source_version="schema-2",
    )
    deleted_resource = _resource(session, source_id, "METRIC:100")
    assert deleted_resource.status == "deleted"
    delete_result = service.process_job(deleted.job_ids[0], provider)
    assert delete_result.activated is True
    assert delete_result.embedded_count == 0

    active_resource_ids = {
        unit.resource_id
        for unit in session.exec(
            select(RetrievalUnitModel)
            .join(
                RetrievalResourceModel,
                col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
            )
            .where(
                RetrievalResourceModel.source_id == source_id,
                RetrievalUnitModel.status == "active",
            )
        ).all()
    }
    assert deleted_resource.id not in active_resource_ids
    assert service.reconcile_source(source_id).issues == ()

    service.rollback_generation(source_id, "generation-1")
    session.refresh(source)
    session.refresh(deleted_resource)
    assert source.active_generation == "generation-1"
    assert source.source_version == "schema-1"
    assert deleted_resource.status == "active"
    assert service.reconcile_source(source_id).issues == ()


def test_generation_status_and_vectors_are_complete_after_activation(session: Session):
    source = _source(session, "indexing-generation-counts")
    source_id = source.id or 0
    service = _service(session)
    provider = _StaticBatchProvider()
    queued = service.enqueue_generation(
        source_id=source_id,
        target_generation="generation-1",
        upserts=_projected_resources(source_version="schema-1"),
        full_rebuild=True,
    )
    processed = []
    while result := service.process_next(provider):
        processed.append(result.job_id)
    assert processed == list(queued.job_ids)
    assert service.process_next(provider) is None

    generation = session.exec(
        select(RetrievalIndexGenerationModel).where(
            RetrievalIndexGenerationModel.source_id == source_id,
            RetrievalIndexGenerationModel.generation == "generation-1",
        )
    ).one()
    embeddings = list(
        session.exec(
            select(RetrievalEmbeddingModel).where(
                RetrievalEmbeddingModel.index_generation == "generation-1"
            )
        ).all()
    )
    assert generation.status == "active"
    assert generation.embedding_profile == "bge-m3-1024"
    assert generation.embedding_provider == "static"
    assert generation.embedding_model == "static-bge-m3"
    assert generation.embedding_dimension == 1024
    assert generation.expected_jobs == 3
    assert generation.succeeded_jobs == 3
    assert generation.failed_jobs == 0
    assert generation.resource_count == 3
    assert generation.unit_count == 8
    assert generation.embedding_count == 8
    assert all(embedding.status == "active" for embedding in embeddings)
    assert all(embedding.embedding is not None and len(embedding.embedding) == 1024 for embedding in embeddings)


def test_semantic_coordinator_writes_source_projection_and_jobs_in_caller_transaction(session: Session):
    domain = SemanticDomain(oid=TENANT_ID, name="交易域", biz_name="trade_domain")
    session.add(domain)
    session.flush()
    model = SemanticModel(
        oid=TENANT_ID,
        domain_id=domain.id or 0,
        datasource_id=99_999,
        name="交易模型",
        biz_name="trade_model",
    )
    session.add(model)
    session.flush()
    dataset = SemanticDataset(
        oid=TENANT_ID,
        domain_id=domain.id or 0,
        name="经营分析",
        biz_name="business_analysis",
        schema_version=3,
        index_version=7,
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": model.id, "includesAll": True, "metrics": [], "dimensions": []}
            ]
        },
    )
    session.add(dataset)
    session.flush()
    metric = SemanticMetric(
        oid=TENANT_ID,
        model_id=model.id or 0,
        name="销售额",
        biz_name="sales_amount",
        description="支付成功订单金额",
        default_agg="SUM",
        expr="pay_amount",
        fields=["pay_amount"],
    )
    session.add(metric)
    session.flush()

    schema = SemanticSchemaService(SemanticSchemaLoader(session)).build_dataset_schema(
        TENANT_ID, dataset.id or 0
    )
    result = SemanticIndexCoordinator(session, PROFILE).enqueue_dataset_rebuild(
        tenant_id=TENANT_ID,
        version=DatasetIndexVersion(
            dataset_id=dataset.id or 0,
            schema_version=dataset.schema_version,
            index_version=dataset.index_version,
        ),
        schema=schema,
    )

    source = session.get(RetrievalSourceModel, result.source_id)
    assert source is not None
    assert source.source_key == f"dataset:{dataset.id}"
    assert source.status == "rebuilding"
    assert source.source_version == "schema:3:index:7"
    assert result.generation == f"dataset-{dataset.id}-index-7"
    assert len(result.job_ids) == 3
    resources = list(
        session.exec(
            select(RetrievalResourceModel)
            .where(RetrievalResourceModel.source_id == result.source_id)
            .order_by(
                RetrievalResourceModel.resource_type,
                RetrievalResourceModel.source_resource_id,
            )
        ).all()
    )
    assert [(resource.resource_type, resource.source_resource_id) for resource in resources] == [
        ("DATASET", f"DATASET:{dataset.id}"),
        ("METRIC", f"METRIC:{metric.id}"),
        ("MODEL", f"MODEL:{model.id}"),
    ]
