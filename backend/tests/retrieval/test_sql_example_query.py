"""SQL_EXEMPLAR profile 的活动 generation 查询测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from sqlmodel import Session

from apps.knowledge.models.dto import (
    SQLExampleRecord,
    SQLExampleSourceSnapshot,
    SQLExampleVerificationStatus,
)
from apps.retrieval.errors import RetrievalProviderUnavailableError
from apps.retrieval.indexing import IndexEmbeddingProfile, RetrievalIndexingService
from apps.retrieval.models.dto import RetrievalChannel, RetrievalChannelStatus
from apps.retrieval.semantic_runtime import RetrievalEmbeddingRuntimeConfig
from apps.retrieval.sql_example_indexing import SQLExampleIndexCoordinator
from apps.retrieval.sql_example_query import (
    SQLExampleRecallCandidate,
    SQLExampleRetriever,
)
from common.core.db import engine

TENANT_ID = 9_940_001
PROFILE = IndexEmbeddingProfile(
    name="bge-m3-1024",
    provider="static",
    model="static-bge-m3",
    dimension=1024,
    batch_size=8,
)


class _ExampleVectorProvider:
    provider = "static"
    model = "static-bge-m3"
    dimension = 1024

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @classmethod
    def _vector(cls, text: str) -> list[float]:
        vector = [0.0] * cls.dimension
        vector[0 if "销售" in text else 1] = 1.0
        return vector


class _TimeoutProvider(_ExampleVectorProvider):
    def embed_query(self, text: str) -> list[float]:
        _ = text
        raise httpx.TimeoutException("query timeout")


class _LexicalOnlyStore:
    def search_lexical(self, *args, **kwargs) -> list[SQLExampleRecallCandidate]:
        _ = args, kwargs
        return [
            SQLExampleRecallCandidate(
                example_id=101,
                channel=RetrievalChannel.LEXICAL,
                score=0.9,
                index_generation="generation-1",
            )
        ]

    def search_dense(self, *args, **kwargs) -> list[SQLExampleRecallCandidate]:
        _ = args, kwargs
        raise AssertionError("provider 失败时不应执行 dense SQL")


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


def _config(*, allow_fallback: bool = True) -> RetrievalEmbeddingRuntimeConfig:
    return RetrievalEmbeddingRuntimeConfig(
        enabled=True,
        provider="sentence_transformers",
        api_base_url="",
        api_key="",
        model="static-bge-m3",
        dimension=1024,
        top_k=20,
        allow_lexical_fallback=allow_fallback,
    )


def _example(
    example_id: int,
    question: str,
    *,
    datasource_id: int | None = None,
    assistant_id: int | None = None,
    enabled: bool = True,
) -> SQLExampleRecord:
    return SQLExampleRecord(
        id=example_id,
        oid=TENANT_ID,
        datasource=datasource_id,
        advanced_application=assistant_id,
        question=question,
        description=f"SELECT {example_id}",
        enabled=enabled,
        verification_status=SQLExampleVerificationStatus.VERIFIED,
    )


def _activate(
    session: Session,
    records: list[SQLExampleRecord],
) -> None:
    queued = SQLExampleIndexCoordinator(session, PROFILE).stage_rebuild(
        SQLExampleSourceSnapshot.from_records(TENANT_ID, records)
    )
    service = RetrievalIndexingService(
        session,
        PROFILE,
        retry_delay=timedelta(0),
    )
    provider = _ExampleVectorProvider()
    for job_id in queued.job_ids:
        service.process_job(job_id, provider)


def test_sql_exemplar_query_reads_active_generation_and_enforces_source_scope(
    session: Session,
):
    _activate(
        session,
        [
            _example(101, "销售额", datasource_id=88),
            _example(102, "销售额", datasource_id=99),
            _example(103, "销售分析", assistant_id=7),
        ],
    )
    retriever = SQLExampleRetriever(
        session,
        embedding_provider=_ExampleVectorProvider(),
        embedding_config=_config(),
    )

    datasource_result = retriever.search(
        TENANT_ID,
        "销售额",
        datasource_id=88,
        assistant_id=None,
    )
    assistant_result = retriever.search(
        TENANT_ID,
        "销售",
        datasource_id=None,
        assistant_id=7,
    )

    assert datasource_result.example_ids == (101,)
    assert assistant_result.example_ids == (103,)
    assert datasource_result.index_generations
    assert [item.status for item in datasource_result.channels] == [
        RetrievalChannelStatus.SUCCEEDED,
        RetrievalChannelStatus.SUCCEEDED,
    ]


def test_sql_exemplar_query_never_reads_resource_removed_from_new_generation(
    session: Session,
):
    _activate(session, [_example(101, "销售额", datasource_id=88)])
    _activate(session, [_example(101, "销售额", datasource_id=88, enabled=False)])
    retriever = SQLExampleRetriever(
        session,
        embedding_provider=_ExampleVectorProvider(),
        embedding_config=_config(),
    )

    assert retriever.search_ids(
        TENANT_ID,
        "销售额",
        datasource_id=88,
        assistant_id=None,
    ) == []


def test_dense_failure_uses_lexical_result_only_when_fallback_is_enabled():
    retriever = SQLExampleRetriever(
        None,
        embedding_provider=_TimeoutProvider(),
        embedding_config=_config(allow_fallback=True),
        store=_LexicalOnlyStore(),  # type: ignore[arg-type]
    )

    result = retriever.search(
        TENANT_ID,
        "销售额",
        datasource_id=88,
        assistant_id=None,
    )

    assert result.example_ids == (101,)
    assert result.channels[1].status == RetrievalChannelStatus.FAILED
    assert result.channels[1].error_code == "EMBEDDING_PROVIDER_TIMEOUT"


def test_dense_failure_is_not_swallowed_when_fallback_is_disabled():
    retriever = SQLExampleRetriever(
        None,
        embedding_provider=_TimeoutProvider(),
        embedding_config=_config(allow_fallback=False),
        store=_LexicalOnlyStore(),  # type: ignore[arg-type]
    )

    with pytest.raises(RetrievalProviderUnavailableError):
        retriever.search(
            TENANT_ID,
            "销售额",
            datasource_id=88,
            assistant_id=None,
        )
