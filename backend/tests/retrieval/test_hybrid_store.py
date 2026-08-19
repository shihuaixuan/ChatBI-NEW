"""统一 generation 上混合召回与硬过滤的 PostgreSQL 集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from apps.retrieval.indexing.service import (
    IndexEmbeddingProfile,
    RetrievalIndexingService,
)
from apps.retrieval.models.dto import (
    RetrievalChannel,
    RetrievalChannelStatus,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalRequest,
    RetrievalResourceType,
    RetrievalScope,
    RetrievalSourceType,
    RetrievalSubQuery,
)
from apps.retrieval.models.orm import RetrievalResourceModel, RetrievalSourceModel
from apps.retrieval.projection.contracts import (
    ProjectedResource,
    ProjectedUnit,
    projection_content_hash,
)
from apps.retrieval.query.hybrid import (
    HybridRetrievalConfig,
    SemanticBindingHybridRetriever,
    SemanticBindingSearchStore,
)
from common.core.db import engine

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
PROFILE = IndexEmbeddingProfile(
    name="bge-m3-1024",
    provider="static",
    model="static-bge-m3",
    dimension=1024,
    batch_size=16,
)


class _SemanticVectorProvider:
    provider = "static"
    model = "static-bge-m3"
    dimension = 1024

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @classmethod
    def _vector(cls, text: str) -> list[float]:
        vector = [0.0] * cls.dimension
        vector[0 if any(word in text for word in ["销售", "营收"]) else 1] = 1.0
        return vector


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


def _source(
    session: Session,
    *,
    tenant_id: int,
    dataset_id: int,
    suffix: str,
) -> RetrievalSourceModel:
    source = RetrievalSourceModel(
        tenant_id=tenant_id,
        source_type="headless",
        source_key=f"dataset:{dataset_id}:{suffix}",
        namespace=f"headless:dataset:{dataset_id}:{suffix}",
        source_version="schema-1",
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(source)
    session.flush()
    return source


def _metric(
    source: RetrievalSourceModel,
    *,
    dataset_id: int,
    asset_id: int,
    title: str,
    aliases: list[str],
    description: str,
    visibility: str = "tenant",
    acl: dict | None = None,
    permission_version: str = "permission-1",
    contextual_text: str | None = None,
) -> ProjectedResource:
    unit = ProjectedUnit.create(
        unit_key="identity",
        content_kind="identity",
        title=title,
        content=f"指标名称：{title}\n指标别名：{'、'.join(aliases)}\n业务定义：{description}",
        contextual_text=contextual_text or f"数据集：{dataset_id}",
        metadata={"asset_id": asset_id, "asset_type": "METRIC", "aliases": aliases},
    )
    metadata = {"asset_id": asset_id, "asset_type": "METRIC", "model_id": 10}
    content_hash = projection_content_hash(
        {
            "title": title,
            "metadata": metadata,
            "acl": acl or {},
            "visibility": visibility,
            "permission_version": permission_version,
            "units": [{"unit_key": unit.unit_key, "content_hash": unit.content_hash}],
        }
    )
    return ProjectedResource(
        tenant_id=source.tenant_id,
        namespace=source.namespace,
        resource_type=RetrievalResourceType.METRIC,
        source_type=RetrievalSourceType.SEMANTIC,
        source_resource_id=f"METRIC:{asset_id}",
        dataset_id=dataset_id,
        title=title,
        metadata=metadata,
        acl=acl or {},
        visibility=visibility,
        permission_version=permission_version,
        source_version="schema-1",
        content_hash=content_hash,
        units=(unit,),
    )


def _activate(
    session: Session,
    source: RetrievalSourceModel,
    resources: list[ProjectedResource],
    provider: _SemanticVectorProvider,
) -> None:
    service = RetrievalIndexingService(
        session,
        PROFILE,
        retry_delay=timedelta(0),
        clock=lambda: NOW,
    )
    queued = service.enqueue_generation(
        source_id=source.id or 0,
        target_generation=f"generation-{source.id}",
        upserts=resources,
        full_rebuild=True,
    )
    for job_id in queued.job_ids:
        service.process_job(job_id, provider)


def _request(text: str) -> RetrievalRequest:
    return RetrievalRequest(
        request_id=f"hybrid-store-{text}",
        tenant_id=9_930_001,
        actor_id=88,
        metric_phrases=[text],
        dimension_phrases=[],
        scope=RetrievalScope(
            dataset_ids=[20],
            principal_roles=["analyst"],
            permission_version="permission-1",
        ),
        profiles=[RetrievalProfileName.SEMANTIC_BINDING],
        strategy_version="semantic-binding",
    )


def _subquery(text: str) -> RetrievalSubQuery:
    return RetrievalSubQuery(
        subquery_id="metric:1",
        purpose=RetrievalPurpose.METRIC,
        text=text,
        filters={},
    )


@pytest.fixture
def indexed_store(session: Session) -> tuple[SemanticBindingSearchStore, _SemanticVectorProvider]:
    provider = _SemanticVectorProvider()
    primary = _source(session, tenant_id=9_930_001, dataset_id=20, suffix="primary")
    primary_resources = [
        _metric(
            primary,
            dataset_id=20,
            asset_id=100,
            title="销售额",
            aliases=["GMV", "成交额"],
            description="销售订单实付金额汇总",
        ),
        _metric(
            primary,
            dataset_id=20,
            asset_id=101,
            title="利润",
            aliases=["毛利"],
            description="收入扣除成本后的利润",
            visibility="private",
            acl={"roles": ["analyst"]},
        ),
        _metric(
            primary,
            dataset_id=20,
            asset_id=102,
            title="成本",
            aliases=["费用"],
            description="订单履约成本",
            visibility="private",
            acl={"roles": ["finance"]},
        ),
        _metric(
            primary,
            dataset_id=20,
            asset_id=103,
            title="客单价",
            aliases=["ATV"],
            description="每个订单平均支付金额",
            permission_version="permission-2",
        ),
        _metric(
            primary,
            dataset_id=20,
            asset_id=104,
            title="已停用指标",
            aliases=["停用测试"],
            description="不应参与召回",
        ),
        _metric(
            primary,
            dataset_id=20,
            asset_id=105,
            title="客户名称",
            aliases=[],
            description="客户的展示名称",
            contextual_text="数据集：商城店铺数据集",
        ),
    ]
    _activate(session, primary, primary_resources, provider)
    inactive = session.exec(
        select(RetrievalResourceModel).where(
            RetrievalResourceModel.source_id == primary.id,
            RetrievalResourceModel.source_resource_id == "METRIC:104",
        )
    ).one()
    inactive.status = "inactive"
    session.flush()

    other_dataset = _source(session, tenant_id=9_930_001, dataset_id=21, suffix="other-dataset")
    _activate(
        session,
        other_dataset,
        [
            _metric(
                other_dataset,
                dataset_id=21,
                asset_id=201,
                title="跨数据集指标",
                aliases=["其他数据集"],
                description="不能跨数据集召回",
            )
        ],
        provider,
    )
    other_tenant = _source(session, tenant_id=9_930_002, dataset_id=20, suffix="other-tenant")
    _activate(
        session,
        other_tenant,
        [
            _metric(
                other_tenant,
                dataset_id=20,
                asset_id=301,
                title="跨租户指标",
                aliases=["其他租户"],
                description="不能跨租户召回",
            )
        ],
        provider,
    )
    return SemanticBindingSearchStore(session, HybridRetrievalConfig()), provider


def test_exact_alias_lexical_and_dense_channels_read_active_generation(indexed_store):
    store, provider = indexed_store
    request = _request("销售额")

    exact = store.search_exact(request, _subquery("销售额"), 20)
    alias = store.search_alias(request, _subquery("GMV"), 20)
    lexical = store.search_lexical(request, _subquery("订单实付金额"), 20)
    dense = store.search_dense(request, _subquery("营收规模"), provider.embed_query("营收规模"), 20)

    assert [item.hit.asset_ref.asset_id for item in exact if item.hit.asset_ref] == [100]
    assert [item.hit.asset_ref.asset_id for item in alias if item.hit.asset_ref] == [100]
    assert lexical[0].hit.asset_ref is not None and lexical[0].hit.asset_ref.asset_id == 100
    assert dense[0].hit.asset_ref is not None and dense[0].hit.asset_ref.asset_id == 100
    assert all(item.hit.provenance["index_generation"] for item in [*exact, *alias, *lexical, *dense])


def test_lexical_channel_does_not_treat_dataset_context_as_asset_identity(indexed_store):
    store, _ = indexed_store

    lexical = store.search_lexical(_request("店铺"), _subquery("店铺"), 20)

    assert all(
        item.hit.asset_ref is None or item.hit.asset_ref.asset_id != 105
        for item in lexical
    )


def test_hard_filters_run_before_channel_recall(indexed_store):
    store, _ = indexed_store

    for hidden_name in ["成本", "客单价", "已停用指标", "跨数据集指标", "跨租户指标"]:
        assert store.search_exact(_request(hidden_name), _subquery(hidden_name), 20) == []


def test_private_acl_role_can_authorize_exact_recall(indexed_store):
    store, _ = indexed_store
    hits = store.search_exact(_request("利润"), _subquery("利润"), 20)

    assert hits[0].hit.asset_ref is not None
    assert hits[0].hit.asset_ref.asset_id == 101


def test_hybrid_retriever_fuses_non_exact_slot_and_reports_generation(indexed_store):
    store, provider = indexed_store
    result = SemanticBindingHybridRetriever(
        session=object(),
        embedding_provider=provider,
        config=HybridRetrievalConfig(),
        store=store,
    ).retrieve(_request("营收规模"))

    assert result.slots[0].fast_path is False
    assert result.slots[0].hits[0].asset_ref is not None
    assert result.slots[0].hits[0].asset_ref.asset_id == 100
    assert result.index_generations
    statuses = {item.channel: item.status for item in result.slots[0].channels}
    assert statuses[RetrievalChannel.LEXICAL] == RetrievalChannelStatus.SUCCEEDED
    assert statuses[RetrievalChannel.DENSE] == RetrievalChannelStatus.SUCCEEDED
