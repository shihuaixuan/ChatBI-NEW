"""Headless Projector 的文本、metadata、安全与增量边界测试。"""

from __future__ import annotations

import hashlib

from apps.headless.models import (
    HeadlessDataSet,
    HeadlessDimension,
    HeadlessMetric,
    HeadlessModel,
)
from apps.headless.schemas import DataSetSchema, JoinRelation, SchemaElement
from apps.headless.service import HeadlessSchemaBuilder
from apps.retrieval.headless_projector import (
    HeadlessProjectionPolicy,
    HeadlessSourceProjector,
)
from apps.retrieval.projection import ProjectedResource, ProjectedResourceDelta
from apps.retrieval.schemas import RetrievalResourceType


def _schema() -> DataSetSchema:
    dataset = SchemaElement(
        data_set_id=20,
        data_set_name="经营分析",
        id=20,
        name="经营分析",
        biz_name="business_analysis",
        type="DATASET",
    )
    return DataSetSchema(
        data_set=dataset,
        subject_domains=[
            {
                "domain_id": 7,
                "name": "商城店铺主题",
                "biz_name": "store_domain",
                "description": "商城店铺经营与交易分析",
                "model_ids": [10, 11],
            }
        ],
        models=[
            {
                "id": 10,
                "name": "交易模型",
                "biz_name": "trade_model",
                "sqlQuery": "SELECT secret_amount FROM secret_orders",
                "filterSql": "tenant_secret = current_user",
                "fields": [{"fieldName": "secret_amount"}],
            },
            {
                "id": 11,
                "name": "门店模型",
                "biz_name": "store_model",
                "tableQuery": "secret_store_table",
            },
        ],
        model_relations=[
            JoinRelation(
                id=300,
                left="trade_model",
                right="store_model",
                join_type="left join",
                join_condition=[["secret_store_id", "=", "secret_store_id"]],
            )
        ],
        metrics=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=100,
                name="销售额",
                biz_name="sales_amount",
                type="METRIC",
                alias=["成交额", "GMV"],
                description="支付成功订单的含税金额",
                default_agg="SUM",
                related_schema_elements=[{"type": "DIMENSION", "id": 200}],
                type_params={
                    "metricDefineType": "MEASURE",
                    "expr": "SUM(secret_amount)",
                    "filterSql": "secret_order_filter = 1",
                },
                fields=["secret_amount"],
                ext_info={"sensitive_level": 0},
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=101,
                name="敏感利润",
                biz_name="secret_profit",
                type="METRIC",
                description="不允许进入检索索引",
                ext_info={"sensitive_level": 2},
            ),
        ],
        dimensions=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=11,
                id=200,
                name="地区",
                biz_name="region_code",
                type="DIMENSION",
                alias=["区域"],
                description="门店所属经营区域",
                ext_info={
                    "dimension_type": "categorical",
                    "semantic_type": "geographic",
                    "field_name": "secret_region_code",
                    "dimension_data_type": "VARCHAR",
                    "sensitive_level": 0,
                },
            ),
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=10,
                id=201,
                name="下单日期",
                biz_name="order_date",
                type="DIMENSION",
                description="订单创建日期",
                ext_info={
                    "dimension_type": "time",
                    "semantic_type": "date",
                    "is_default_time": True,
                    "time_granularities": ["DAY", "MONTH"],
                    "sensitive_level": 0,
                },
            ),
        ],
        terms=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=-1,
                id=300,
                name="成交规模",
                biz_name="成交规模",
                type="TERM",
                alias=["生意规模"],
                description="描述已支付交易的业务规模",
                related_schema_elements=[
                    {"type": "METRIC", "id": 100},
                    {"type": "DIMENSION", "id": 200},
                ],
            )
        ],
        dimension_values=[
            SchemaElement(
                data_set_id=20,
                data_set_name="经营分析",
                model=11,
                id=200,
                name="地区",
                biz_name="region_code",
                type="VALUE",
                schema_value_maps=[
                    {
                        "value": "EAST_SECRET_CODE",
                        "bizName": "华东",
                        "alias": ["东区"],
                    },
                    {
                        "value": "NORTH_SECRET_CODE",
                        "bizName": "华北",
                        "isCommon": True,
                    },
                    {
                        "value": "UNCONTROLLED_SECRET_CODE",
                        "bizName": "随机区域",
                    },
                ],
            )
        ],
    )


def _project(
    schema: DataSetSchema | None = None,
    projector: HeadlessSourceProjector | None = None,
    source_version: str = "schema-7",
) -> list[ProjectedResource]:
    return (projector or HeadlessSourceProjector()).project(
        schema or _schema(),
        tenant_id=1,
        namespace="headless:dataset:20",
        source_version=source_version,
        acl={"roles": ["analyst"], "row_filter": "secret_acl_condition"},
        visibility="private",
        permission_version="permission-3",
    )


def _by_type(resources: list[ProjectedResource], resource_type: RetrievalResourceType) -> ProjectedResource:
    matches = [resource for resource in resources if resource.resource_type == resource_type]
    assert len(matches) == 1
    return matches[0]


def _unit_snapshot(resource: ProjectedResource) -> list[dict]:
    return [
        {
            "unit_key": unit.unit_key,
            "content_kind": unit.content_kind,
            "title": unit.title,
            "content": unit.content,
            "contextual_text": unit.contextual_text,
            "metadata": unit.metadata,
        }
        for unit in resource.units
    ]


def test_dataset_and_models_are_projected_with_subject_domain_content():
    resources = _project()
    dataset = _by_type(resources, RetrievalResourceType.DATASET)
    models = [resource for resource in resources if resource.resource_type == RetrievalResourceType.MODEL]

    assert dataset.source_resource_id == "DATASET:20"
    assert dataset.metadata == {
        "asset_type": "DATASET",
        "asset_id": 20,
        "dataset_id": 20,
        "biz_name": "business_analysis",
        "subject_domain_ids": [7],
        "model_ids": [10, 11],
    }
    domain_unit = next(unit for unit in dataset.units if unit.unit_key == "subject-domain:7")
    assert domain_unit.title == "商城店铺主题"
    assert domain_unit.embedding_text == (
        "商城店铺主题\n"
        "主题域名称：商城店铺主题\n"
        "主题域定义：商城店铺经营与交易分析\n"
        "数据集：经营分析"
    )
    assert [(model.source_resource_id, model.title) for model in models] == [
        ("MODEL:10", "交易模型"),
        ("MODEL:11", "门店模型"),
    ]
    trade_scope = next(unit for unit in models[0].units if unit.unit_key == "scope")
    assert "所属主题域：商城店铺主题" in trade_scope.content
    assert "可用指标：销售额" in trade_scope.content
    assert "可用维度：下单日期" in trade_scope.content


def test_metric_projector_snapshot_keeps_relationships_structured():
    metric = _by_type(_project(), RetrievalResourceType.METRIC)

    assert metric.model_dump(exclude={"units", "content_hash"}) == {
        "tenant_id": 1,
        "namespace": "headless:dataset:20",
        "resource_type": RetrievalResourceType.METRIC,
        "source_type": "headless",
        "source_resource_id": "METRIC:100",
        "dataset_id": 20,
        "knowledge_base_id": None,
        "title": "销售额",
        "metadata": {
            "asset_type": "METRIC",
            "asset_id": 100,
            "dataset_id": 20,
            "biz_name": "sales_amount",
            "model_id": 10,
            "same_model_dimension_ids": [201],
            "joinable_model_ids": [11],
            "compatible_dimension_ids": [200, 201],
            "model_relations": [
                {
                    "relation_id": 300,
                    "left_model_id": 10,
                    "right_model_id": 11,
                    "join_type": "left join",
                }
            ],
        },
        "acl": {"roles": ["analyst"], "row_filter": "secret_acl_condition"},
        "visibility": "private",
        "permission_version": "permission-3",
        "source_version": "schema-7",
    }
    assert _unit_snapshot(metric) == [
        {
            "unit_key": "definition",
            "content_kind": "definition",
            "title": "销售额定义",
            "content": "指标定义：支付成功订单的含税金额",
            "contextual_text": "数据集：经营分析",
            "metadata": {
                "asset_type": "METRIC",
                "asset_id": 100,
                "dataset_id": 20,
                "biz_name": "sales_amount",
                "model_id": 10,
            },
        },
        {
            "unit_key": "identity",
            "content_kind": "identity",
            "title": "销售额",
            "content": "指标名称：销售额\n指标别名：GMV、成交额",
            "contextual_text": "数据集：经营分析",
            "metadata": {
                "asset_type": "METRIC",
                "asset_id": 100,
                "dataset_id": 20,
                "biz_name": "sales_amount",
                "model_id": 10,
                "aliases": ["GMV", "成交额"],
            },
        },
        {
            "unit_key": "usage",
            "content_kind": "usage",
            "title": "销售额用法",
            "content": "默认聚合：SUM\n指标形态：MEASURE",
            "contextual_text": "数据集：经营分析",
            "metadata": {
                "asset_type": "METRIC",
                "asset_id": 100,
                "dataset_id": 20,
                "biz_name": "sales_amount",
                "model_id": 10,
                "default_agg": "SUM",
                "related_dimension_ids": [200],
                "same_model_dimension_ids": [201],
                "joinable_model_ids": [11],
                "compatible_dimension_ids": [200, 201],
                "model_relations": [
                    {
                        "relation_id": 300,
                        "left_model_id": 10,
                        "right_model_id": 11,
                        "join_type": "left join",
                    }
                ],
            },
        },
    ]


def test_dimension_term_and_value_projector_snapshots():
    resources = _project()
    dimensions = {
        resource.source_resource_id: resource
        for resource in resources
        if resource.resource_type == RetrievalResourceType.DIMENSION
    }
    region = dimensions["DIMENSION:200"]
    term = _by_type(resources, RetrievalResourceType.TERM)
    values = _by_type(resources, RetrievalResourceType.VALUE)

    assert [unit.content_kind for unit in region.units] == ["definition", "identity", "role"]
    assert region.units[2].model_dump(
        exclude={"content_hash", "embedding_text_hash", "language"}
    ) == {
        "unit_key": "role",
        "content_kind": "role",
        "title": "地区角色",
        "content": "维度类型：categorical\n语义类型：geographic\n业务角色：分析维度",
        "contextual_text": "数据集：经营分析",
        "metadata": {
            "asset_type": "DIMENSION",
            "asset_id": 200,
            "dataset_id": 20,
            "biz_name": "region_code",
            "model_id": 11,
            "dimension_type": "categorical",
            "semantic_type": "geographic",
            "is_primary_key": False,
            "is_default_time": False,
            "time_granularities": [],
            "same_model_dimension_ids": [200],
            "joinable_model_ids": [10],
            "compatible_dimension_ids": [200, 201],
            "model_relations": [
                {
                    "relation_id": 300,
                    "left_model_id": 10,
                    "right_model_id": 11,
                    "join_type": "left join",
                }
            ],
        },
    }
    assert _unit_snapshot(term) == [
        {
            "unit_key": "definition",
            "content_kind": "definition",
            "title": "成交规模",
            "content": "术语名称：成交规模\n术语别名：生意规模\n术语定义：描述已支付交易的业务规模",
            "contextual_text": "数据集：经营分析",
            "metadata": {
                "asset_type": "TERM",
                "asset_id": 300,
                "dataset_id": 20,
                "biz_name": "成交规模",
                "aliases": ["生意规模"],
            },
        },
        {
            "unit_key": "relationships",
            "content_kind": "relationships",
            "title": "成交规模关联资产",
            "content": "关联资产类型：指标、维度",
            "contextual_text": "数据集：经营分析",
            "metadata": {
                "asset_type": "TERM",
                "asset_id": 300,
                "dataset_id": 20,
                "biz_name": "成交规模",
                "related_metric_ids": [100],
                "related_dimension_ids": [200],
            },
        },
    ]
    assert values.metadata == {
        "asset_type": "VALUE",
        "asset_id": 200,
        "dimension_id": 200,
        "model_id": 11,
        "dataset_id": 20,
    }
    assert {unit.title for unit in values.units} == {"华东", "华北"}
    assert {unit.metadata["canonical_value"] for unit in values.units} == {
        "EAST_SECRET_CODE",
        "NORTH_SECRET_CODE",
    }


def test_projection_excludes_execution_secrets_and_sensitive_assets_from_embedding_text():
    resources = _project()
    embedding_text = "\n".join(unit.embedding_text for resource in resources for unit in resource.units)

    for forbidden in [
        "敏感利润",
        "SUM(secret_amount)",
        "secret_amount",
        "secret_orders",
        "tenant_secret",
        "secret_store_table",
        "secret_store_id",
        "secret_region_code",
        "secret_acl_condition",
        "EAST_SECRET_CODE",
        "NORTH_SECRET_CODE",
        "UNCONTROLLED_SECRET_CODE",
    ]:
        assert forbidden not in embedding_text
    assert "join_condition" not in str([resource.metadata for resource in resources])


def test_schema_builder_exposes_sensitive_level_to_the_single_projection_gate():
    model = HeadlessModel(
        id=10,
        oid=1,
        domain_id=1,
        datasource_id=7,
        name="交易模型",
        biz_name="trade_model",
    )
    dataset = HeadlessDataSet(
        id=20,
        oid=1,
        domain_id=1,
        name="经营分析",
        biz_name="business_analysis",
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": 10, "includesAll": True, "metrics": [], "dimensions": []}
            ]
        },
    )
    schema = HeadlessSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=None,
        models=[model],
        metrics=[
            HeadlessMetric(
                id=100,
                oid=1,
                model_id=10,
                name="敏感指标",
                biz_name="secret_metric",
                sensitive_level=2,
                expr="secret_amount",
                fields=["secret_amount"],
            )
        ],
        dimensions=[
            HeadlessDimension(
                id=200,
                oid=1,
                model_id=10,
                name="敏感维度",
                biz_name="secret_dimension",
                sensitive_level=1,
            )
        ],
        terms=[],
    )

    assert schema.metrics[0].ext_info["sensitive_level"] == 2
    assert schema.dimensions[0].ext_info["sensitive_level"] == 1
    resources = HeadlessSourceProjector().project(
        schema,
        tenant_id=1,
        namespace="headless:dataset:20",
        source_version="schema-1",
    )
    assert {resource.resource_type for resource in resources} == {
        RetrievalResourceType.DATASET,
        RetrievalResourceType.MODEL,
    }


def test_content_hash_is_deterministic_and_alias_change_only_rebuilds_identity_unit():
    before_schema = _schema()
    before = _by_type(_project(before_schema), RetrievalResourceType.METRIC)
    repeated = _by_type(_project(before_schema), RetrievalResourceType.METRIC)
    assert before == repeated
    assert all(len(unit.content_hash) == 64 for unit in before.units)

    changed_schema = before_schema.model_copy(deep=True)
    changed_schema.metrics[0].alias = ["成交额", "流水", "GMV"]
    after = _by_type(_project(changed_schema), RetrievalResourceType.METRIC)
    delta = ProjectedResourceDelta.between(before, after)

    assert delta.resource_changed is True
    assert delta.upsert_unit_keys == ("identity",)
    assert delta.reembed_unit_keys == ("identity",)
    assert delta.delete_unit_keys == ()
    assert delta.unchanged_unit_keys == ("definition", "usage")

    version_only = _by_type(_project(before_schema, source_version="schema-8"), RetrievalResourceType.METRIC)
    version_delta = ProjectedResourceDelta.between(before, version_only)
    assert version_delta.resource_changed is True
    assert version_delta.upsert_unit_keys == ()
    assert version_delta.reembed_unit_keys == ()
    assert version_delta.unchanged_unit_keys == ("definition", "identity", "usage")


def test_relationship_metadata_change_updates_unit_without_reembedding_text():
    before_schema = _schema()
    before = _by_type(_project(before_schema), RetrievalResourceType.METRIC)
    changed_schema = before_schema.model_copy(deep=True)
    changed_schema.model_relations[0].join_type = "inner join"
    after = _by_type(_project(changed_schema), RetrievalResourceType.METRIC)

    delta = ProjectedResourceDelta.between(before, after)

    assert delta.upsert_unit_keys == ("usage",)
    assert delta.reembed_unit_keys == ()
    assert delta.unchanged_unit_keys == ("definition", "identity")


def test_value_projection_requires_governance_common_flag_or_explicit_low_cardinality_policy():
    schema = _schema()
    default_values = _by_type(_project(schema), RetrievalResourceType.VALUE)
    assert "随机区域" not in {unit.title for unit in default_values.units}

    policy = HeadlessProjectionPolicy(
        configured_value_dimension_ids=frozenset({200}),
        max_configured_values_per_dimension=3,
    )
    configured_values = _by_type(
        _project(schema, projector=HeadlessSourceProjector(policy)),
        RetrievalResourceType.VALUE,
    )
    assert {unit.title for unit in configured_values.units} == {"华东", "华北", "随机区域"}
    expected_key = hashlib.sha256(b"UNCONTROLLED_SECRET_CODE").hexdigest()[:24]
    assert f"value:{expected_key}" in {unit.unit_key for unit in configured_values.units}

    limited_policy = HeadlessProjectionPolicy(
        configured_value_dimension_ids=frozenset({200}),
        max_configured_values_per_dimension=2,
    )
    limited_values = _by_type(
        _project(schema, projector=HeadlessSourceProjector(limited_policy)),
        RetrievalResourceType.VALUE,
    )
    assert "随机区域" not in {unit.title for unit in limited_values.units}
