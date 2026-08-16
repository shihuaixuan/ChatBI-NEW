"""Knowledge SQL 示例来源投影与 generation 生命周期测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlmodel import Session, col, select

from apps.knowledge.models.dto import (
    SQLExampleRecord,
    SQLExampleSourceSnapshot,
    SQLExampleVerificationStatus,
)
from apps.retrieval.indexing.service import (
    IndexEmbeddingProfile,
    RetrievalIndexingService,
    RetryableIndexingError,
)
from apps.retrieval.models.dto import RetrievalResourceType, RetrievalSourceType
from apps.retrieval.models.orm import (
    RetrievalIndexGenerationModel,
    RetrievalResourceModel,
    RetrievalSourceModel,
    RetrievalUnitModel,
)
from apps.retrieval.sources.sql_example_indexing import SQLExampleIndexCoordinator
from apps.retrieval.sources.sql_example_projector import SQLExampleSourceProjector
from common.core.db import engine

TENANT_ID = 9_930_001
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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1)] * self.dimension for index, _ in enumerate(texts)]


class _RetryableFailureProvider(_StaticBatchProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        _ = texts
        raise RetryableIndexingError(
            "EMBEDDING_PROVIDER_TIMEOUT",
            "批量 embedding 超时",
        )


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


def _snapshot(*records: SQLExampleRecord) -> SQLExampleSourceSnapshot:
    return SQLExampleSourceSnapshot.from_records(TENANT_ID, list(records))


def _example(
    example_id: int,
    *,
    description: str = "SELECT SUM(amount) FROM orders",
    enabled: bool = True,
    semantic_plan: dict | None = None,
) -> SQLExampleRecord:
    return SQLExampleRecord(
        id=example_id,
        oid=TENANT_ID,
        datasource=88,
        question=f"示例问题 {example_id}",
        description=description,
        example_type="QUESTION_EXAMPLE",
        sql="SELECT SUM(amount) FROM orders WHERE paid = true",
        linked_assets=[{"asset_type": "METRIC", "asset_id": 100}],
        dataset_id=20,
        enabled=enabled,
        verification_status=SQLExampleVerificationStatus.VERIFIED,
        semantic_plan=semantic_plan,
        plan_fingerprint="plan-fingerprint" if semantic_plan else None,
    )


def _run_jobs(
    session: Session,
    job_ids: tuple[int, ...],
    provider: _StaticBatchProvider,
    *,
    max_attempts: int = 3,
) -> None:
    service = RetrievalIndexingService(
        session,
        PROFILE,
        max_attempts=max_attempts,
        retry_delay=timedelta(0),
    )
    for job_id in job_ids:
        service.process_job(job_id, provider)


def test_projector_only_reads_public_snapshot_and_preserves_scope_metadata():
    snapshot = _snapshot(_example(101))

    resources = SQLExampleSourceProjector().project(
        snapshot,
        namespace=f"knowledge:workspace:{TENANT_ID}:sql-examples",
    )

    assert len(resources) == 1
    resource = resources[0]
    assert resource.resource_type == RetrievalResourceType.SQL_EXEMPLAR
    assert resource.source_type == RetrievalSourceType.SQL_EXEMPLAR
    assert resource.source_resource_id == "101"
    assert resource.dataset_id == 20
    assert resource.metadata["datasource_id"] == 88
    assert resource.metadata["linked_assets"] == [
        {"asset_type": "METRIC", "asset_id": 100}
    ]
    assert resource.metadata["verification_status"] == "VERIFIED"
    assert resource.units[0].content == "SELECT SUM(amount) FROM orders"
    assert "WHERE paid = true" in resource.units[0].contextual_text


def test_projector_exposes_plan_summary_without_sql_or_physical_tables():
    snapshot = _snapshot(
        _example(
            102,
            semantic_plan={
                "metrics": [{"metric_id": 100, "aggregation": "SUM"}],
                "dimensions": [{"logical_dimension_id": 200}],
                "filters": [
                    {
                        "physical_dimension_id": 201,
                        "operator": "=",
                        "value": "EAST",
                    }
                ],
                "time_binding": {"time_range": {"start": "2026-08-01"}},
                "query_shape": {"needs_group_by": True},
                "sql": "SELECT secret FROM physical_table",
                "physical_table": "physical_table",
            },
        )
    )

    resource = SQLExampleSourceProjector().project(
        snapshot,
        namespace=f"knowledge:workspace:{TENANT_ID}:sql-examples",
    )[0]
    summary = resource.metadata["semantic_plan_summary"]

    assert summary["metric_ids"] == [100]
    assert summary["dimension_ids"] == [200]
    assert summary["filters"][0]["value"] == "EAST"
    assert "sql" not in summary
    assert "physical_table" not in summary


def test_source_version_is_stable_for_same_enabled_snapshot():
    first = _snapshot(_example(102), _example(101))
    second = _snapshot(
        _example(101),
        _example(999, enabled=False),
        _example(102),
    )

    assert first.source_version == second.source_version
    assert [item.id for item in first.examples] == [101, 102]


def test_unverified_example_is_excluded_from_retrieval_snapshot():
    unverified = _example(103).model_copy(
        update={
            "linked_assets": [{"legacy_invalid": True}],
            "verification_status": SQLExampleVerificationStatus.UNVERIFIED,
        }
    )

    snapshot = _snapshot(_example(101), unverified)

    assert [item.id for item in snapshot.examples] == [101]


def test_full_snapshot_disables_missing_resource_and_activates_new_generation(
    session: Session,
):
    coordinator = SQLExampleIndexCoordinator(session, PROFILE)
    first = coordinator.stage_rebuild(_snapshot(_example(101), _example(102)))
    _run_jobs(session, first.job_ids, _StaticBatchProvider())

    second = coordinator.stage_rebuild(
        _snapshot(_example(101), _example(102, enabled=False))
    )
    _run_jobs(session, second.job_ids, _StaticBatchProvider())

    source = session.get(RetrievalSourceModel, second.source_id)
    assert source is not None
    assert source.source_key == f"workspace:{TENANT_ID}:sql-examples"
    assert source.active_generation == second.generation
    resources = list(
        session.exec(
            select(RetrievalResourceModel)
            .where(RetrievalResourceModel.source_id == second.source_id)
            .order_by(col(RetrievalResourceModel.source_resource_id))
        ).all()
    )
    assert [(item.source_resource_id, item.status) for item in resources] == [
        ("101", "active"),
        ("102", "deleted"),
    ]
    active_units = list(
        session.exec(
            select(RetrievalUnitModel)
            .join(
                RetrievalResourceModel,
                col(RetrievalResourceModel.id) == col(RetrievalUnitModel.resource_id),
            )
            .where(
                RetrievalResourceModel.source_id == second.source_id,
                RetrievalUnitModel.status == "active",
            )
        ).all()
    )
    assert len(active_units) == 1


def test_failed_sql_example_generation_keeps_previous_generation_active(
    session: Session,
):
    coordinator = SQLExampleIndexCoordinator(session, PROFILE)
    first = coordinator.stage_rebuild(_snapshot(_example(101)))
    _run_jobs(session, first.job_ids, _StaticBatchProvider())

    second = coordinator.stage_rebuild(
        _snapshot(_example(101, description="SELECT COUNT(*) FROM orders"))
    )
    _run_jobs(
        session,
        second.job_ids,
        _RetryableFailureProvider(),
        max_attempts=1,
    )

    source = session.get(RetrievalSourceModel, second.source_id)
    assert source is not None
    assert source.active_generation == first.generation
    assert source.status == "active"
    failed_generation = session.exec(
        select(RetrievalIndexGenerationModel).where(
            RetrievalIndexGenerationModel.source_id == second.source_id,
            RetrievalIndexGenerationModel.generation == second.generation,
        )
    ).one()
    assert failed_generation.status == "failed"
    assert failed_generation.error_code == "EMBEDDING_PROVIDER_TIMEOUT"
