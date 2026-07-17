from apps.semantic.asset_document import SemanticAssetDocumentBuilder
from apps.semantic.models import SemanticAssetDocument
from apps.semantic.schemas import DatasetSchema, SchemaElement


def _element(asset_type: str, asset_id: int, name: str, biz_name: str, **kwargs):
    return SchemaElement(
        data_set_id=1,
        data_set_name="客户数据集",
        id=asset_id,
        name=name,
        biz_name=biz_name,
        type=asset_type,
        alias=kwargs.pop("alias", []),
        description=kwargs.pop("description", None),
        fields=kwargs.pop("fields", []),
        type_params=kwargs.pop("type_params", {}),
        ext_info=kwargs.pop("ext_info", {}),
        schema_value_maps=kwargs.pop("schema_value_maps", []),
    )


def test_asset_document_model_and_builder_from_schema():
    schema = DatasetSchema(
        database_type="mysql",
        data_set=_element("DATASET", 1, "客户数据集", "customer_ds"),
        metrics=[
            _element(
                "METRIC",
                10,
                "访问人数",
                "visit_uv",
                alias=["UV"],
                description="访问去重人数",
                fields=["visit_uv"],
                type_params={"expr": "visit_uv"},
            )
        ],
        dimensions=[
            _element(
                "DIMENSION",
                20,
                "性别",
                "gender",
                alias=["用户性别"],
                ext_info={"dimension_type": "categorical"},
            )
        ],
        dimension_values=[
            _element(
                "VALUE",
                20,
                "性别",
                "gender",
                alias=["女性", "女士"],
                schema_value_maps=[{"value": "女", "bizName": "女性", "alias": ["女士"]}],
            )
        ],
        terms=[_element("TERM", 30, "人气", "人气", alias=["热度"], description="访问相关指标")],
    )

    documents = SemanticAssetDocumentBuilder().build_from_schema(schema, oid=1, index_version=3)

    assert all(isinstance(item, SemanticAssetDocument) for item in documents)
    metric_doc = next(item for item in documents if item.asset_type == "METRIC")
    value_doc = next(item for item in documents if item.asset_type == "VALUE")
    assert metric_doc.doc_key == "METRIC:10"
    assert "访问去重人数" in metric_doc.business_text
    assert "visit_uv" in metric_doc.technical_text
    assert "UV" in metric_doc.alias_text
    assert value_doc.payload["schema_value_maps"][0]["value"] == "女"
    assert value_doc.index_version == 3


def test_asset_document_debug_route_is_registered():
    from apps.semantic.api import router

    paths = {route.path for route in router.routes}

    assert "/semantic/datasets/{dataset_id}/asset-documents" in paths
