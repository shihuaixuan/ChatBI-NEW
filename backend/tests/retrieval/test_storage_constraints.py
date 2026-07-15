"""PostgreSQL 对统一检索关键约束的真实执行测试。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from common.core.db import engine


@pytest.fixture
def connection() -> Iterator[Connection]:
    with engine.connect() as value:
        transaction = value.begin()
        try:
            yield value
        finally:
            transaction.rollback()


def _seed_source(connection: Connection, *, tenant_id: int, source_key: str) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO retrieval_source (
                tenant_id, source_type, source_key, namespace, source_version, status
            ) VALUES (
                :tenant_id, 'headless', :source_key, 'semantic', 'v1', 'active'
            )
            RETURNING id
            """
        ),
        {"tenant_id": tenant_id, "source_key": source_key},
    ).scalar_one()


def _seed_resource(connection: Connection, *, tenant_id: int, source_id: int, resource_key: str) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO retrieval_resource (
                tenant_id, namespace, resource_type, source_id, source_resource_id,
                dataset_id, title, source_version, content_hash, status
            ) VALUES (
                :tenant_id, 'semantic', 'METRIC', :source_id, :resource_key,
                243, '销售额', 'v1', :content_hash, 'active'
            )
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "source_id": source_id,
            "resource_key": resource_key,
            "content_hash": "a" * 64,
        },
    ).scalar_one()


def _seed_generation(
    connection: Connection,
    *,
    tenant_id: int,
    source_id: int,
    generation: str,
    status: str = "building",
) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO retrieval_index_generation (
                tenant_id, source_id, generation, source_version, embedding_profile,
                embedding_provider, embedding_model, embedding_dimension, status
            ) VALUES (
                :tenant_id, :source_id, :generation, 'v1', 'bge-m3-1024',
                'test', 'BAAI/bge-m3', 1024, :status
            )
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "source_id": source_id,
            "generation": generation,
            "status": status,
        },
    ).scalar_one()


def _seed_unit(
    connection: Connection,
    *,
    tenant_id: int,
    resource_id: int,
    generation: str,
    status: str = "pending",
) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO retrieval_unit (
                tenant_id, resource_id, unit_key, content_kind, title, content,
                content_hash, index_generation, status
            ) VALUES (
                :tenant_id, :resource_id, 'identity', 'identity', '销售额',
                '指标名称：销售额', :content_hash, :generation, :status
            )
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "resource_id": resource_id,
            "content_hash": "b" * 64,
            "generation": generation,
            "status": status,
        },
    ).scalar_one()


def test_same_logical_unit_can_coexist_in_two_pending_generations(connection: Connection):
    tenant_id = 9_910_001
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="generation-source")
    resource_id = _seed_resource(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        resource_key="metric:100",
    )

    first = _seed_unit(
        connection,
        tenant_id=tenant_id,
        resource_id=resource_id,
        generation="generation-a",
    )
    second = _seed_unit(
        connection,
        tenant_id=tenant_id,
        resource_id=resource_id,
        generation="generation-b",
    )

    assert first != second


def test_duplicate_active_generation_is_rejected(connection: Connection):
    tenant_id = 9_910_002
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="active-source")
    resource_id = _seed_resource(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        resource_key="metric:101",
    )
    _seed_unit(
        connection,
        tenant_id=tenant_id,
        resource_id=resource_id,
        generation="generation-a",
        status="active",
    )

    with pytest.raises(IntegrityError):
        _seed_unit(
            connection,
            tenant_id=tenant_id,
            resource_id=resource_id,
            generation="generation-b",
            status="active",
        )


def test_cross_tenant_source_reference_is_rejected(connection: Connection):
    source_id = _seed_source(connection, tenant_id=9_910_003, source_key="tenant-source")

    with pytest.raises(IntegrityError):
        _seed_resource(
            connection,
            tenant_id=9_910_004,
            source_id=source_id,
            resource_key="metric:102",
        )


def test_embedding_generation_must_match_its_unit(connection: Connection):
    tenant_id = 9_910_005
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="embedding-generation-source")
    resource_id = _seed_resource(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        resource_key="metric:103",
    )
    unit_id = _seed_unit(
        connection,
        tenant_id=tenant_id,
        resource_id=resource_id,
        generation="generation-a",
    )

    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO retrieval_embedding (
                    tenant_id, unit_id, embedding_profile, provider, model, dimension,
                    text_hash, index_generation, status
                ) VALUES (
                    :tenant_id, :unit_id, 'bge-m3-1024', 'test', 'BAAI/bge-m3', 1024,
                    :text_hash, 'generation-b', 'pending'
                )
                """
            ),
            {"tenant_id": tenant_id, "unit_id": unit_id, "text_hash": "c" * 64},
        )


def test_embedding_dimension_mismatch_is_rejected(connection: Connection):
    tenant_id = 9_910_006
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="embedding-dimension-source")
    resource_id = _seed_resource(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        resource_key="metric:104",
    )
    unit_id = _seed_unit(
        connection,
        tenant_id=tenant_id,
        resource_id=resource_id,
        generation="generation-a",
    )

    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO retrieval_embedding (
                    tenant_id, unit_id, embedding_profile, provider, model, dimension,
                    text_hash, index_generation, status
                ) VALUES (
                    :tenant_id, :unit_id, 'wrong-dimension', 'test', 'test-model', 768,
                    :text_hash, 'generation-a', 'pending'
                )
                """
            ),
            {"tenant_id": tenant_id, "unit_id": unit_id, "text_hash": "d" * 64},
        )


def test_only_one_active_index_generation_is_allowed_per_source(connection: Connection):
    tenant_id = 9_910_007
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="active-index-generation")
    _seed_generation(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        generation="generation-a",
        status="active",
    )

    with pytest.raises(IntegrityError):
        _seed_generation(
            connection,
            tenant_id=tenant_id,
            source_id=source_id,
            generation="generation-b",
            status="active",
        )


def test_index_job_must_reference_same_tenant_generation(connection: Connection):
    tenant_id = 9_910_008
    source_id = _seed_source(connection, tenant_id=tenant_id, source_key="job-generation-source")
    _seed_generation(
        connection,
        tenant_id=tenant_id,
        source_id=source_id,
        generation="generation-a",
    )

    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO retrieval_index_job (
                    tenant_id, source_id, operation, target_generation, status
                ) VALUES (
                    :tenant_id, :source_id, 'rebuild', 'generation-missing', 'pending'
                )
                """
            ),
            {"tenant_id": tenant_id, "source_id": source_id},
        )
