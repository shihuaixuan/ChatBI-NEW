"""Headless 数据集跨业务域 schema 组装测试。"""

from typing import Any

from apps.headless.models import (
    HeadlessDataSet,
    HeadlessDataSetAsset,
    HeadlessDataSetModelConfig,
    HeadlessDimension,
    HeadlessDimensionValue,
    HeadlessDomain,
    HeadlessMetric,
    HeadlessModel,
    HeadlessModelField,
    HeadlessModelMeasure,
    HeadlessModelRelation,
    HeadlessTerm,
)
from apps.headless.service import HeadlessSchemaBuilder


class _Result:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _DatasetSchemaSession:
    def __init__(
        self,
        dataset: HeadlessDataSet,
        domains: list[HeadlessDomain],
        models: list[HeadlessModel],
        metrics: list[HeadlessMetric],
        dimensions: list[HeadlessDimension],
        configs: list[HeadlessDataSetModelConfig],
    ) -> None:
        self.dataset = dataset
        self.domains = {domain.id: domain for domain in domains}
        self.models = models
        self.metrics = metrics
        self.dimensions = dimensions
        self.configs = configs

    def get(self, entity: type[Any], entity_id: int) -> Any:
        if entity is HeadlessDataSet and entity_id == self.dataset.id:
            return self.dataset
        if entity is HeadlessDomain:
            return self.domains.get(entity_id)
        return None

    def exec(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0].get("entity")
        statement_text = str(statement)
        if entity is HeadlessDataSetModelConfig:
            return _Result(self.configs)
        if entity is HeadlessDataSetAsset:
            return _Result([])
        if entity is HeadlessModel:
            if "headless_model.id IN" in statement_text:
                return _Result(self.models)
            return _Result([model for model in self.models if model.domain_id == self.dataset.domain_id])
        if entity is HeadlessMetric:
            return _Result(self.metrics)
        if entity is HeadlessDimension:
            return _Result(self.dimensions)
        if entity in {
            HeadlessDimensionValue,
            HeadlessModelField,
            HeadlessModelMeasure,
            HeadlessModelRelation,
            HeadlessTerm,
        }:
            return _Result([])
        return _Result([])


def test_schema_builder_uses_dataset_model_configs_across_domains():
    shop_domain = HeadlessDomain(
        id=1,
        oid=1,
        name="店铺",
        biz_name="shop",
        description="店铺与档口经营主题",
    )
    product_domain = HeadlessDomain(
        id=2,
        oid=1,
        name="商品",
        biz_name="product",
        description="商品经营主题",
    )
    dataset = HeadlessDataSet(id=20, oid=1, domain_id=1, name="经营分析", biz_name="business_bi")
    shop_model = HeadlessModel(
        id=10,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="店铺宽表",
        biz_name="shop_wide",
        table_name="shop_wide",
    )
    product_model = HeadlessModel(
        id=11,
        oid=1,
        domain_id=2,
        datasource_id=7,
        name="商品宽表",
        biz_name="product_wide",
        table_name="product_wide",
    )
    shop_metric = HeadlessMetric(
        id=100,
        oid=1,
        model_id=10,
        name="访问人数",
        biz_name="shop_visit_uv",
        fields=["visit_uv"],
    )
    product_metric = HeadlessMetric(
        id=101,
        oid=1,
        model_id=11,
        name="访问人数",
        biz_name="product_visit_uv",
        fields=["visit_uv"],
    )
    session = _DatasetSchemaSession(
        dataset=dataset,
        domains=[shop_domain, product_domain],
        models=[shop_model, product_model],
        metrics=[shop_metric, product_metric],
        dimensions=[],
        configs=[
            HeadlessDataSetModelConfig(
                oid=1,
                dataset_id=20,
                model_id=10,
                includes_all=True,
                sort_order=1,
            ),
            HeadlessDataSetModelConfig(
                oid=1,
                dataset_id=20,
                model_id=11,
                includes_all=True,
                sort_order=2,
            ),
        ],
    )

    schema = HeadlessSchemaBuilder(session).build_dataset_schema(oid=1, dataset_id=20)

    assert [model["id"] for model in schema.models] == [10, 11]
    assert [metric.biz_name for metric in schema.metrics] == ["shop_visit_uv", "product_visit_uv"]
    assert schema.subject_domains == [
        {
            "domain_id": 1,
            "name": "店铺",
            "biz_name": "shop",
            "description": "店铺与档口经营主题",
            "model_ids": [10],
        },
        {
            "domain_id": 2,
            "name": "商品",
            "biz_name": "product",
            "description": "商品经营主题",
            "model_ids": [11],
        },
    ]
