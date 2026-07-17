from apps.semantic.asset_relation import (
    build_aliases_for_asset,
    build_metric_relations,
    build_term_relations,
)
from apps.semantic.models import (
    SemanticAssetAlias,
    SemanticAssetRelation,
    SemanticMetric,
    SemanticTerm,
)


def test_asset_alias_model_and_builder_create_unique_aliases():
    metric = SemanticMetric(
        id=10,
        oid=1,
        model_id=2,
        name="访问人数",
        biz_name="visit_uv",
        alias=["UV", "访客数", "UV"],
    )

    aliases = build_aliases_for_asset("METRIC", metric.id, metric.oid, metric.alias)

    assert isinstance(aliases[0], SemanticAssetAlias)
    assert [item.alias for item in aliases] == ["UV", "访客数"]
    assert aliases[0].asset_type == "METRIC"
    assert aliases[0].alias_type == "MANUAL"


def test_metric_relations_include_measure_field_metric_and_dimensions():
    metric = SemanticMetric(
        id=10,
        oid=1,
        model_id=2,
        name="转化率",
        biz_name="convert_rate",
        measure_id=20,
        field_id=30,
        metric_refs=[11, 12],
        relate_dimensions=[{"id": 40}, {"dimensionId": 41}],
    )

    relations = build_metric_relations(metric)

    assert isinstance(relations[0], SemanticAssetRelation)
    assert {(item.relation_type, item.target_type, item.target_id) for item in relations} == {
        ("DEFINED_BY_MEASURE", "MEASURE", 20),
        ("USES_FIELD", "FIELD", 30),
        ("DEFINED_BY_METRIC", "METRIC", 11),
        ("DEFINED_BY_METRIC", "METRIC", 12),
        ("ANALYZABLE_BY", "DIMENSION", 40),
        ("ANALYZABLE_BY", "DIMENSION", 41),
    }


def test_term_relations_link_related_metrics_and_dimensions():
    term = SemanticTerm(
        id=7,
        oid=1,
        domain_id=1,
        name="人气",
        related_metrics=[10],
        related_dimensions=[20],
    )

    relations = build_term_relations(term)

    assert {(item.source_type, item.relation_type, item.target_type, item.target_id) for item in relations} == {
        ("TERM", "RELATED_TO", "METRIC", 10),
        ("TERM", "RELATED_TO", "DIMENSION", 20),
    }
