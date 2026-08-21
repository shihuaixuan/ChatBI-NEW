from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
)
from apps.semantic.services.builders.schema_builder import SemanticSchemaBuilder
from apps.semantic.services.matching.schema_element_matcher import SchemaElementMatcher


def test_schema_mapper_matches_metric_dimension_and_dimension_value_alias():
    model = SemanticModel(id=10, oid=1, domain_id=1, datasource_id=7, name="模型", biz_name="model")
    metric = SemanticMetric(
        id=100,
        oid=1,
        model_id=10,
        name="咨询人数",
        biz_name="stall_inquiry_uv",
        alias=["咨询UV"],
    )
    dimension = SemanticDimension(
        id=200,
        oid=1,
        model_id=10,
        name="档口",
        biz_name="stall_id",
        dim_value_maps=[{"value": "1", "bizName": "1号档口", "alias": ["一号档口"]}],
    )
    dataset = SemanticDataset(
        id=20,
        oid=1,
        domain_id=1,
        name="档口经营分析",
        biz_name="stall_bi",
        data_set_detail={
            "dataSetModelConfigs": [{"id": 10, "includesAll": True, "metrics": [], "dimensions": []}]
        },
    )
    schema = SemanticSchemaBuilder().build_from_assets(
        dataset=dataset,
        domain=None,
        models=[model],
        metrics=[metric],
        dimensions=[dimension],
        terms=[],
        dataset_model_configs=[
            SemanticDatasetModelConfig(
                oid=1, dataset_id=20, model_id=10, includes_all=True
            )
        ],
        dataset_assets=[],
    )

    map_info = SchemaElementMatcher().match("一号档口咨询UV是多少", schema)
    matches = map_info.data_set_element_matches[20]

    assert [match.element.type for match in matches] == ["METRIC", "VALUE"]
    assert matches[0].detect_word == "咨询UV"
    assert matches[1].word == "1"
