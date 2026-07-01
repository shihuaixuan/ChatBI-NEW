from apps.headless.metric_embedding import StaticEmbeddingProvider, build_metric_embedding_text
from apps.headless.models import HeadlessAssetEmbedding, HeadlessMetric


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
