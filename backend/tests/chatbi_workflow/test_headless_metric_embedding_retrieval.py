from apps.chatbi_workflow.capabilities.adapters.knowledge import (
    HeadlessDocumentRetriever,
)
from apps.headless.metric_embedding import StaticEmbeddingProvider
from apps.headless.schemas import DataSetSchema, SchemaElement


def _element(asset_type: str, asset_id: int, name: str, biz_name: str, **kwargs):
    return SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        model=kwargs.pop("model", 10),
        id=asset_id,
        name=name,
        biz_name=biz_name,
        type=asset_type,
        alias=kwargs.pop("alias", []),
        description=kwargs.pop("description", None),
    )


class _MetricEmbeddingSession:
    def execute(self, _statement, _params=None):
        return [(100, 0.91)]


def test_headless_document_retriever_merges_metric_embedding_hits():
    schema = DataSetSchema(
        data_set=_element("DATASET", 20, "经营分析", "business"),
        metrics=[_element("METRIC", 100, "销售额", "sales_amount", description="订单实付金额汇总")],
        dimensions=[_element("DIMENSION", 200, "地区", "region")],
    )
    retriever = HeadlessDocumentRetriever(
        metric_embedding_session=_MetricEmbeddingSession(),
        metric_embedding_provider=StaticEmbeddingProvider(vector=[1.0, 0.0], provider="test", model="fake"),
    )

    groups = retriever.retrieve("GMV 趋势", schema, oid=1)

    assert groups["metrics"][0]["asset_id"] == 100
    assert groups["metrics"][0]["source"] == "headless_metric_embedding"
    assert groups["dimensions"] == []
