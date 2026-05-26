from datetime import datetime

import pytest
from sqlalchemy import delete
from sqlmodel import Session

import apps.semantic.crud.metric as metric_crud
import apps.semantic.services.semantic_embedding as semantic_embedding
import apps.semantic.services.semantic_search as semantic_search
from apps.chat.models.chat_model import Chat, ChatRecord
from apps.semantic.crud.metric import approve_metric, create_metric
from apps.semantic.crud.usage import list_chat_record_assets, save_chat_asset_usage
from apps.semantic.models.semantic_model import (
    AssetStatus,
    AssetType,
    ChatRecordSemanticAsset,
    SemanticDimension,
    SemanticMetric,
    UsageRole,
)
from apps.semantic.models.semantic_schema import (
    MetricCreate,
    SemanticAssetMatch,
    SemanticRetrieveRequest,
)
from apps.semantic.services.asset_usage import (
    save_matched_and_injected_semantic_assets,
    save_used_semantic_assets,
)
from apps.semantic.services.semantic_context import build_prompt_context
from apps.semantic.services.semantic_embedding import (
    build_dimension_embedding_text,
    build_metric_embedding_text,
    rebuild_dimension_embedding,
    rebuild_metric_embedding,
    rebuild_missing_approved_embeddings,
)
from apps.semantic.services.semantic_search import (
    alias_score,
    embedding_score,
    invalidate_semantic_asset_cache,
    retrieve_semantic_assets,
)
from common.core.db import engine


def create_chat_record(session: Session, oid: int) -> tuple[Chat, ChatRecord]:
    chat = Chat(
        oid=oid,
        create_time=datetime.now(),
        create_by=1,
        brief="semantic usage test",
        chat_type="chat",
        datasource=1,
        engine_type="PostgreSQL",
    )
    session.add(chat)
    session.flush()
    session.refresh(chat)
    record = ChatRecord(
        chat_id=chat.id,
        create_time=datetime.now(),
        create_by=1,
        datasource=1,
        engine_type="PostgreSQL",
        question="最近 7 天销售额趋势",
    )
    session.add(record)
    session.flush()
    session.refresh(record)
    return chat, record


def cleanup_chat_record(session: Session, chat: Chat, record: ChatRecord):
    session.execute(delete(ChatRecordSemanticAsset).where(ChatRecordSemanticAsset.record_id == record.id))
    session.execute(delete(ChatRecord).where(ChatRecord.id == record.id))
    session.execute(delete(Chat).where(Chat.id == chat.id))
    session.commit()


def test_save_chat_asset_usage_persists_all_roles_and_updates_existing_snapshot():
    oid = 9401

    with Session(engine) as session:
        chat, record = create_chat_record(session, oid)
        match = SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=1001,
            name="sales_amount",
            display_name="销售额",
            score=0.92,
            match_word="销售额",
            snapshot={"display_name": "销售额", "expr": "pay_amount"},
        )

        save_chat_asset_usage(session, record.id, [match])
        session.commit()
        save_chat_asset_usage(
            session,
            record.id,
            [
                SemanticAssetMatch(
                    asset_type=AssetType.METRIC.value,
                    asset_id=1001,
                    name="sales_amount",
                    display_name="销售额",
                    score=0.98,
                    match_word="销售额",
                    snapshot={"display_name": "销售额", "expr": "paid_amount"},
                )
            ],
        )
        session.commit()

        assets = list_chat_record_assets(session, oid=oid, record_id=record.id)

        assert [asset.role for asset in assets] == [
            UsageRole.MATCHED.value,
            UsageRole.INJECTED.value,
            UsageRole.USED.value,
        ]
        assert all(asset.score == 0.98 for asset in assets)
        assert all(asset.snapshot["expr"] == "paid_amount" for asset in assets)

        cleanup_chat_record(session, chat, record)


def test_list_chat_record_assets_rejects_records_outside_oid():
    with Session(engine) as session:
        chat, record = create_chat_record(session, oid=9402)
        save_chat_asset_usage(
            session,
            record.id,
            [
                SemanticAssetMatch(
                    asset_type=AssetType.DIMENSION.value,
                    asset_id=2001,
                    name="region",
                    display_name="区域",
                    score=0.8,
                    snapshot={"display_name": "区域", "expr": "region"},
                )
            ],
        )
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_PERMISSION_DENIED"):
            list_chat_record_assets(session, oid=9999, record_id=record.id)

        cleanup_chat_record(session, chat, record)


def test_save_matched_and_injected_semantic_assets_saves_only_injected_when_context_exists():
    oid = 9406

    with Session(engine) as session:
        chat, record = create_chat_record(session, oid)
        match = SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=1002,
            name="sales_amount",
            display_name="销售额",
            score=0.92,
            match_word="销售额",
            snapshot={"display_name": "销售额", "name": "sales_amount", "expr": "pay_amount"},
        )

        save_matched_and_injected_semantic_assets(session, record.id, [match], semantic_context="")
        session.commit()

        assets = list_chat_record_assets(session, oid=oid, record_id=record.id)
        assert [asset.role for asset in assets] == [UsageRole.MATCHED.value]

        save_matched_and_injected_semantic_assets(session, record.id, [match], semantic_context="已审核业务指标")
        session.commit()

        assets = list_chat_record_assets(session, oid=oid, record_id=record.id)
        assert [asset.role for asset in assets] == [UsageRole.MATCHED.value, UsageRole.INJECTED.value]
        assert all(asset.snapshot["expr"] == "pay_amount" for asset in assets)

        cleanup_chat_record(session, chat, record)


def test_save_used_semantic_assets_saves_only_assets_referenced_by_generated_sql():
    oid = 9407

    with Session(engine) as session:
        chat, record = create_chat_record(session, oid)
        used_match = SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=1003,
            name="sales_amount",
            display_name="销售额",
            score=0.92,
            snapshot={
                "display_name": "销售额",
                "name": "sales_amount",
                "expr": "pay_amount",
                "default_agg": "SUM",
            },
        )
        unused_match = SemanticAssetMatch(
            asset_type=AssetType.DIMENSION.value,
            asset_id=2003,
            name="region",
            display_name="区域",
            score=0.88,
            snapshot={
                "display_name": "区域",
                "name": "region",
                "expr": "region",
                "dimension_type": "CATEGORY",
                "semantic_type": "STRING",
            },
        )

        save_used_semantic_assets(
            session,
            record.id,
            [used_match, unused_match],
            "SELECT SUM(pay_amount) AS sales_amount FROM orders",
        )
        session.commit()

        assets = list_chat_record_assets(session, oid=oid, record_id=record.id)
        assert [asset.role for asset in assets] == [UsageRole.USED.value]
        assert assets[0].asset_id == used_match.asset_id
        assert assets[0].snapshot["default_agg"] == "SUM"

        cleanup_chat_record(session, chat, record)


def test_build_metric_embedding_text_is_stable_complete_and_sanitized():
    metric = SemanticMetric(
        name="sales_amount",
        display_name="销售额",
        aliases=["GMV", "收入"],
        description="已支付订单金额 postgresql://user:secret@localhost/db password=secret",
        expr="SUM(pay_amount)",
        default_agg="SUM",
    )

    text = build_metric_embedding_text(metric)

    assert text == build_metric_embedding_text(metric)
    assert "销售额" in text
    assert "sales_amount" in text
    assert "GMV" in text
    assert "收入" in text
    assert "已支付订单金额" in text
    assert "SUM(pay_amount)" in text
    assert "default_agg: SUM" in text
    assert "secret" not in text
    assert "postgresql://user" not in text


def test_build_dimension_embedding_text_handles_empty_optional_fields_and_sanitizes_detail_data():
    dimension = SemanticDimension(
        name="order_date",
        display_name="下单日期",
        aliases=[],
        description=None,
        expr="order_date",
        dimension_type="TIME",
        semantic_type="DATE",
        default_values=["2026-05-25", "2026-05-26"],
    )

    text = build_dimension_embedding_text(dimension)

    assert text == build_dimension_embedding_text(dimension)
    assert "下单日期" in text
    assert "order_date" in text
    assert "dimension_type: TIME" in text
    assert "semantic_type: DATE" in text
    assert "2026-05-25" not in text


class FakeEmbeddingModel:
    def __init__(self, embedding=None):
        self.embedding = embedding or [0.11, 0.22, 0.33]

    def embed_query(self, text: str):
        assert text
        return self.embedding


class FailingEmbeddingModel:
    def embed_query(self, text: str):
        raise RuntimeError("embedding unavailable")


class QueryEmbeddingModel:
    def embed_query(self, text: str):
        assert text
        return [1.0, 0.0, 0.0]


def test_rebuild_metric_embedding_updates_embedding_vector():
    oid = 9403
    datasource_id = 94030001
    name = "metric_rebuild_embedding"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name == name,
            )
        )
        session.commit()
        metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=name,
            display_name="销售额",
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
        )
        session.add(metric)
        session.commit()
        session.refresh(metric)

        rebuild_metric_embedding(session, metric.id, embedding_model=FakeEmbeddingModel())
        session.refresh(metric)

        assert list(metric.embedding) == [0.11, 0.22, 0.33]

        session.execute(delete(SemanticMetric).where(SemanticMetric.id == metric.id))
        session.commit()


def test_rebuild_metric_embedding_overwrites_existing_embedding():
    oid = 9405
    datasource_id = 94050001
    name = "metric_rebuild_embedding_overwrite"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name == name,
            )
        )
        session.commit()
        metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=name,
            display_name="销售额",
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
            embedding=[0.01, 0.02, 0.03],
        )
        session.add(metric)
        session.commit()
        session.refresh(metric)

        rebuild_metric_embedding(session, metric.id, embedding_model=FakeEmbeddingModel([0.44, 0.55, 0.66]))
        session.refresh(metric)

        assert list(metric.embedding) == [0.44, 0.55, 0.66]

        session.execute(delete(SemanticMetric).where(SemanticMetric.id == metric.id))
        session.commit()


def test_rebuild_dimension_embedding_failure_does_not_change_asset_status(monkeypatch):
    oid = 9404
    datasource_id = 94040001
    name = "dimension_rebuild_embedding"
    recorded = []
    monkeypatch.setattr(
        semantic_embedding,
        "record_semantic_metric",
        lambda metric_name, **fields: recorded.append((metric_name, fields)),
        raising=False,
    )

    with Session(engine) as session:
        session.execute(
            delete(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.datasource_id == datasource_id,
                SemanticDimension.name == name,
            )
        )
        session.commit()
        dimension = SemanticDimension(
            oid=oid,
            datasource_id=datasource_id,
            name=name,
            display_name="下单日期",
            expr="order_date",
            status=AssetStatus.APPROVED.value,
        )
        session.add(dimension)
        session.commit()
        session.refresh(dimension)

        before_failed_total = semantic_embedding.semantic_embedding_failed_total

        rebuild_dimension_embedding(session, dimension.id, embedding_model=FailingEmbeddingModel())
        session.refresh(dimension)

        assert dimension.status == AssetStatus.APPROVED.value
        assert dimension.embedding is None
        assert semantic_embedding.semantic_embedding_failed_total == before_failed_total + 1
        assert recorded[0][0] == "semantic_embedding_failed_total"
        assert recorded[0][1]["asset_type"] == "DIMENSION"
        assert recorded[0][1]["asset_id"] == dimension.id

        session.execute(delete(SemanticDimension).where(SemanticDimension.id == dimension.id))
        session.commit()


def test_rebuild_missing_approved_embeddings_respects_limit_and_skips_disabled_assets():
    oid = 9406
    datasource_id = 94060001
    name_prefix = "missing_embedding_compensation"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name.ilike(f"{name_prefix}%"),
            )
        )
        session.execute(
            delete(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.datasource_id == datasource_id,
                SemanticDimension.name.ilike(f"{name_prefix}%"),
            )
        )
        session.commit()
        metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_metric",
            display_name="销售额",
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
        )
        disabled_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_disabled_metric",
            display_name="禁用销售额",
            expr="pay_amount",
            status=AssetStatus.DISABLED.value,
        )
        dimension = SemanticDimension(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_dimension",
            display_name="下单日期",
            expr="order_date",
            status=AssetStatus.APPROVED.value,
        )
        session.add(metric)
        session.add(disabled_metric)
        session.add(dimension)
        session.commit()

        result = rebuild_missing_approved_embeddings(session, limit=1, embedding_model=FakeEmbeddingModel())
        session.refresh(metric)
        session.refresh(disabled_metric)
        session.refresh(dimension)

        assert result["processed"] == 1
        assert sum(asset.embedding is not None for asset in [metric, dimension]) == 1
        assert disabled_metric.embedding is None

        session.execute(delete(SemanticMetric).where(SemanticMetric.id.in_([metric.id, disabled_metric.id])))
        session.execute(delete(SemanticDimension).where(SemanticDimension.id == dimension.id))
        session.commit()


def test_alias_score_prefers_exact_display_name_name_alias_over_description():
    metric = SemanticMetric(
        name="sales_amount",
        display_name="销售额",
        aliases=["GMV"],
        description="订单支付金额",
    )

    assert alias_score("最近 7 天销售额趋势", metric) > 0.9
    assert alias_score("哪个渠道 GMV 最高", metric) > 0.8
    assert alias_score("订单支付金额趋势", metric) < alias_score("最近 7 天销售额趋势", metric)
    assert alias_score("库存数量", metric) == 0


def test_retrieve_semantic_assets_matches_keywords_and_only_approved_assets():
    oid = 9407
    datasource_id = 94070001
    name_prefix = "semantic_keyword_search"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name.ilike(f"{name_prefix}%"),
            )
        )
        session.execute(
            delete(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.datasource_id == datasource_id,
                SemanticDimension.name.ilike(f"{name_prefix}%"),
            )
        )
        session.commit()
        approved_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_sales_amount",
            display_name="销售额",
            aliases=["GMV"],
            description="订单支付金额",
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
        )
        candidate_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_candidate_sales",
            display_name="候选销售额",
            expr="pay_amount",
            status=AssetStatus.CANDIDATE.value,
        )
        disabled_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_disabled_sales",
            display_name="禁用销售额",
            expr="pay_amount",
            status=AssetStatus.DISABLED.value,
        )
        channel_dimension = SemanticDimension(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_channel",
            display_name="渠道",
            aliases=["来源"],
            expr="channel",
            status=AssetStatus.APPROVED.value,
        )
        session.add(approved_metric)
        session.add(candidate_metric)
        session.add(disabled_metric)
        session.add(channel_dimension)
        session.commit()

        sales_response = retrieve_semantic_assets(
            session,
            SemanticRetrieveRequest(
                oid=oid,
                datasource_id=datasource_id,
                question="最近 7 天销售额趋势",
            ),
        )
        gmv_response = retrieve_semantic_assets(
            session,
            SemanticRetrieveRequest(
                oid=oid,
                datasource_id=datasource_id,
                question="哪个渠道 GMV 最高",
            ),
        )

        assert [match.display_name for match in sales_response.metrics] == ["销售额"]
        assert [match.display_name for match in gmv_response.metrics] == ["销售额"]
        assert [match.display_name for match in gmv_response.dimensions] == ["渠道"]
        assert all("候选" not in match.display_name for match in sales_response.metrics)
        assert all("禁用" not in match.display_name for match in sales_response.metrics)

        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.id.in_([approved_metric.id, candidate_metric.id, disabled_metric.id])
            )
        )
        session.execute(delete(SemanticDimension).where(SemanticDimension.id == channel_dimension.id))
        session.commit()


def test_embedding_score_uses_cosine_similarity():
    assert embedding_score([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert embedding_score([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert embedding_score([], [1, 0, 0]) == 0


def test_retrieve_semantic_assets_uses_hybrid_score_and_filters_low_score_assets():
    oid = 9408
    datasource_id = 94080001
    name_prefix = "semantic_hybrid_search"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name.ilike(f"{name_prefix}%"),
            )
        )
        session.commit()
        matched_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_sales_amount",
            display_name="销售额",
            aliases=["GMV"],
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
            embedding=[1.0, 0.0, 0.0],
        )
        low_score_metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=f"{name_prefix}_inventory",
            display_name="库存数量",
            expr="inventory_count",
            status=AssetStatus.APPROVED.value,
            embedding=[0.0, 1.0, 0.0],
        )
        session.add(matched_metric)
        session.add(low_score_metric)
        session.commit()

        response = retrieve_semantic_assets(
            session,
            SemanticRetrieveRequest(
                oid=oid,
                datasource_id=datasource_id,
                question="最近 7 天销售额趋势",
            ),
            embedding_model=QueryEmbeddingModel(),
        )

        assert response.degraded is False
        assert [match.display_name for match in response.metrics] == ["销售额"]
        assert response.metrics[0].score == pytest.approx(0.9)

        session.execute(delete(SemanticMetric).where(SemanticMetric.id.in_([matched_metric.id, low_score_metric.id])))
        session.commit()


def test_retrieve_semantic_assets_degrades_to_keyword_matching_when_embedding_fails():
    oid = 9409
    datasource_id = 94090001
    name = "semantic_embedding_degraded_sales"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name == name,
            )
        )
        session.commit()
        metric = SemanticMetric(
            oid=oid,
            datasource_id=datasource_id,
            name=name,
            display_name="销售额",
            aliases=["GMV"],
            expr="pay_amount",
            status=AssetStatus.APPROVED.value,
            embedding=[1.0, 0.0, 0.0],
        )
        session.add(metric)
        session.commit()

        response = retrieve_semantic_assets(
            session,
            SemanticRetrieveRequest(
                oid=oid,
                datasource_id=datasource_id,
                question="最近 7 天销售额趋势",
            ),
            embedding_model=FailingEmbeddingModel(),
        )

        assert response.degraded is True
        assert response.metrics[0].display_name == "销售额"
        assert response.metrics[0].score == pytest.approx(1.0)

        session.execute(delete(SemanticMetric).where(SemanticMetric.id == metric.id))
        session.commit()


def test_retrieve_semantic_assets_caches_empty_approved_asset_lists(monkeypatch):
    semantic_search.clear_semantic_asset_cache()
    load_calls = []

    def fake_load(_session, _oid, _datasource_id):
        load_calls.append(1)
        return [], []

    monkeypatch.setattr(semantic_search, "_load_approved_assets", fake_load)
    request = SemanticRetrieveRequest(oid=9410, datasource_id=94100001, question="销售额")

    retrieve_semantic_assets(None, request)
    retrieve_semantic_assets(None, request)

    assert len(load_calls) == 1


def test_invalidate_semantic_asset_cache_forces_reload(monkeypatch):
    semantic_search.clear_semantic_asset_cache()
    load_calls = []
    metric = SemanticMetric(
        id=1,
        oid=9411,
        datasource_id=94110001,
        name="sales_amount",
        display_name="销售额",
        expr="pay_amount",
        status=AssetStatus.APPROVED.value,
    )

    def fake_load(_session, _oid, _datasource_id):
        load_calls.append(1)
        return ([metric] if len(load_calls) > 1 else []), []

    monkeypatch.setattr(semantic_search, "_load_approved_assets", fake_load)
    request = SemanticRetrieveRequest(oid=9411, datasource_id=94110001, question="销售额")

    first_response = retrieve_semantic_assets(None, request)
    invalidate_semantic_asset_cache(9411, 94110001)
    second_response = retrieve_semantic_assets(None, request)

    assert first_response.metrics == []
    assert [match.display_name for match in second_response.metrics] == ["销售额"]
    assert len(load_calls) == 2


def test_approved_metric_is_retrievable_immediately_after_cache_invalidation(monkeypatch):
    monkeypatch.setattr(metric_crud, "submit_metric_embedding_rebuild", lambda _metric_id: None)
    semantic_search.clear_semantic_asset_cache()
    oid = 9412
    datasource_id = 94120001
    name = "semantic_cache_approved_sales"

    with Session(engine) as session:
        session.execute(
            delete(SemanticMetric).where(
                SemanticMetric.oid == oid,
                SemanticMetric.datasource_id == datasource_id,
                SemanticMetric.name == name,
            )
        )
        session.commit()
        request = SemanticRetrieveRequest(oid=oid, datasource_id=datasource_id, question="销售额")

        empty_response = retrieve_semantic_assets(session, request)
        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                name=name,
                display_name="销售额",
                expr="pay_amount",
            ),
            user_id=1,
        )
        session.commit()
        approve_metric(session, oid=oid, metric_id=metric.id, user_id=1)
        session.commit()
        approved_response = retrieve_semantic_assets(session, request)

        assert empty_response.metrics == []
        assert [match.display_name for match in approved_response.metrics] == ["销售额"]

        session.execute(delete(SemanticMetric).where(SemanticMetric.id == metric.id))
        session.commit()
        semantic_search.clear_semantic_asset_cache()


def test_regression_questions_hit_only_approved_semantic_assets():
    semantic_search.clear_semantic_asset_cache()
    oid = 9413
    datasource_id = 94130001

    with Session(engine) as session:
        session.execute(delete(SemanticMetric).where(SemanticMetric.oid == oid, SemanticMetric.datasource_id == datasource_id))
        session.execute(delete(SemanticDimension).where(SemanticDimension.oid == oid, SemanticDimension.datasource_id == datasource_id))
        session.commit()
        metrics = [
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="sales_amount", display_name="销售额", aliases=["销售额趋势"], expr="pay_amount", status=AssetStatus.APPROVED.value),
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="order_count", display_name="订单量", aliases=["订单数"], expr="order_id", status=AssetStatus.APPROVED.value),
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="gmv", display_name="GMV", aliases=["渠道 GMV"], expr="pay_amount", status=AssetStatus.APPROVED.value),
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="customer_growth", display_name="客户增长", aliases=["客户增长怎么样"], expr="customer_id", status=AssetStatus.APPROVED.value),
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="refund_amount", display_name="退款金额", aliases=["退款金额"], expr="refund_amount", status=AssetStatus.APPROVED.value),
            SemanticMetric(oid=oid, datasource_id=datasource_id, name="candidate_metric", display_name="未审核指标", expr="candidate_amount", status=AssetStatus.CANDIDATE.value),
        ]
        dimensions = [
            SemanticDimension(oid=oid, datasource_id=datasource_id, name="order_date", display_name="下单日期", aliases=["最近 7 天", "趋势"], expr="order_date", status=AssetStatus.APPROVED.value),
            SemanticDimension(oid=oid, datasource_id=datasource_id, name="region", display_name="区域", aliases=["区域"], expr="region", status=AssetStatus.APPROVED.value),
            SemanticDimension(oid=oid, datasource_id=datasource_id, name="channel", display_name="渠道", aliases=["渠道"], expr="channel", status=AssetStatus.APPROVED.value),
            SemanticDimension(oid=oid, datasource_id=datasource_id, name="category", display_name="商品类目", aliases=["商品类目"], expr="category", status=AssetStatus.APPROVED.value),
            SemanticDimension(oid=oid, datasource_id=datasource_id, name="disabled_dimension", display_name="禁用维度", expr="disabled_dimension", status=AssetStatus.DISABLED.value),
        ]
        session.add_all([*metrics, *dimensions])
        session.commit()

        expectations = {
            "最近 7 天销售额趋势": "销售额",
            "按区域看订单量": "订单量",
            "哪个渠道 GMV 最高": "GMV",
            "客户增长怎么样": "客户增长",
            "按商品类目看退款金额": "退款金额",
        }
        for question, expected_metric in expectations.items():
            response = retrieve_semantic_assets(
                session,
                SemanticRetrieveRequest(oid=oid, datasource_id=datasource_id, question=question),
            )
            assert expected_metric in [match.display_name for match in response.metrics]

        response = retrieve_semantic_assets(
            session,
            SemanticRetrieveRequest(oid=oid, datasource_id=datasource_id, question="未审核指标和禁用维度"),
        )
        context = build_prompt_context(response)
        assert "未审核指标" not in context
        assert "禁用维度" not in context

        session.execute(delete(SemanticMetric).where(SemanticMetric.oid == oid, SemanticMetric.datasource_id == datasource_id))
        session.execute(delete(SemanticDimension).where(SemanticDimension.oid == oid, SemanticDimension.datasource_id == datasource_id))
        session.commit()
        semantic_search.clear_semantic_asset_cache()
