import apps.headless.metric_embedding as metric_embedding
from apps.headless.metric_embedding import (
    StaticEmbeddingProvider,
    build_metric_embedding_text,
    rebuild_dataset_metric_embeddings,
)
from apps.headless.models import HeadlessAssetEmbedding, HeadlessDataSet, HeadlessMetric


def test_headless_asset_embedding_model_defaults():
    record = HeadlessAssetEmbedding(
        oid=1,
        dataset_id=20,
        asset_type="METRIC",
        asset_id=100,
        embedding_text="指标名称: 销售额",
        embedding_text_hash="hash-sales",
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dim=1024,
        embedding=[0.1, 0.2, 0.3],
    )

    assert record.status == "SUCCEEDED"
    assert record.embedding_batch_id is None
    assert record.error_message is None


def test_static_embedding_provider_returns_configured_vector():
    provider = StaticEmbeddingProvider(vector=[0.4, 0.5], provider="test", model="fake-model")

    vector = provider.embed_query("指标名称: 销售额")

    assert vector == [0.4, 0.5]
    assert provider.provider == "test"
    assert provider.model == "fake-model"
    assert provider.dimension == 2


def test_build_metric_embedding_text_keeps_only_name_alias_description():
    metric = HeadlessMetric(
        oid=1,
        model_id=10,
        name="销售额",
        biz_name="sales_amount",
        alias=["GMV", "成交额", "GMV", ""],
        description="  订单实付金额汇总  ",
        expr="sum(pay_amount)",
        fields=["pay_amount"],
        default_agg="SUM",
    )

    text = build_metric_embedding_text(metric)

    assert text == "指标名称: 销售额\n指标别名: GMV, 成交额\n指标说明: 订单实付金额汇总"
    assert "sales_amount" not in text
    assert "pay_amount" not in text
    assert "SUM" not in text


def test_build_metric_embedding_text_omits_empty_lines():
    metric = HeadlessMetric(
        oid=1,
        model_id=10,
        name="销售额",
        biz_name="sales_amount",
        alias=[],
        description="",
    )

    assert build_metric_embedding_text(metric) == "指标名称: 销售额"


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _RebuildSession:
    def __init__(self):
        self.added = []
        self.executed = []
        self.committed = False

    def get(self, model_cls, entity_id):
        if model_cls is HeadlessDataSet and entity_id == 20:
            return HeadlessDataSet(id=20, oid=1, domain_id=1, name="经营分析", biz_name="business")
        return None

    def exec(self, statement):
        self.executed.append(str(statement))
        return _ScalarResult([])

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed = True


def test_rebuild_dataset_metric_embeddings_deletes_old_metric_embeddings_first(monkeypatch):
    metric = HeadlessMetric(
        id=100,
        oid=1,
        model_id=10,
        name="销售额",
        biz_name="sales_amount",
        alias=["GMV"],
        description="订单实付金额汇总",
    )
    session = _RebuildSession()
    provider = StaticEmbeddingProvider(vector=[0.1, 0.2], provider="test", model="fake-model")
    monkeypatch.setattr(metric_embedding, "_load_metrics_for_dataset", lambda *_args: [metric])
    monkeypatch.setattr(metric_embedding, "_document_id_by_metric", lambda *_args: {100: 900})

    result = rebuild_dataset_metric_embeddings(session, oid=1, dataset_id=20, provider=provider)

    assert result["deleted"] is True
    assert result["processed"] == 1
    assert result["succeeded"] == 1
    assert session.committed is True
    assert any("DELETE FROM headless_asset_embedding" in item for item in session.executed)
    record = next(item for item in session.added if isinstance(item, HeadlessAssetEmbedding))
    assert record.asset_type == "METRIC"
    assert record.asset_id == 100
    assert record.document_id == 900
    assert record.embedding_text == "指标名称: 销售额\n指标别名: GMV\n指标说明: 订单实付金额汇总"
    assert record.embedding == [0.1, 0.2]
