import pytest

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticDomain,
    SemanticMetric,
    SemanticModel,
    SemanticTerm,
)
from apps.terminology.models.terminology_model import Terminology
from scripts.migrate_legacy_terminology import (
    build_legacy_snapshots,
    build_migration_context,
    parse_default_domains,
)


def test_parse_default_domains_rejects_conflicting_values():
    with pytest.raises(ValueError, match="多个默认主题域"):
        parse_default_domains(["1:10", "1:11"])


def test_build_legacy_snapshots_merges_child_words():
    rows = [
        Terminology(
            id=7,
            oid=1,
            word="人气",
            aliases=["访问热度"],
            dataset_ids=[20],
        ),
        Terminology(id=8, oid=1, pid=7, word="热度"),
    ]

    snapshots = build_legacy_snapshots(rows)

    assert len(snapshots) == 1
    assert snapshots[0].other_words == ("热度",)
    assert snapshots[0].aliases == ("访问热度",)
    assert snapshots[0].dataset_ids == (20,)


def test_build_migration_context_resolves_asset_domains_and_existing_terms():
    context = build_migration_context(
        domains=[SemanticDomain(id=10, oid=1, name="经营域", biz_name="business")],
        datasets=[
            SemanticDataset(
                id=20,
                oid=1,
                domain_id=10,
                name="经营数据集",
                biz_name="business_dataset",
            )
        ],
        dataset_model_configs=[
            SemanticDatasetModelConfig(
                id=25,
                oid=1,
                dataset_id=20,
                model_id=30,
            )
        ],
        models=[
            SemanticModel(
                id=30,
                oid=1,
                domain_id=10,
                datasource_id=40,
                name="经营模型",
                biz_name="business_model",
            )
        ],
        metrics=[
            SemanticMetric(
                id=100,
                oid=1,
                model_id=30,
                name="销售额",
                biz_name="sales_amount",
            )
        ],
        dimensions=[
            SemanticDimension(
                id=200,
                oid=1,
                model_id=30,
                name="店铺",
                biz_name="shop",
            )
        ],
        terms=[
            SemanticTerm(
                id=300,
                oid=1,
                domain_id=10,
                name="人气",
                related_datasets=[20],
            )
        ],
        default_domains={1: 10},
    )

    assert context.dataset_domains[(1, 20)] == 10
    assert context.datasource_datasets[(1, 40)] == (20,)
    assert context.metric_domains[(1, 100)] == 10
    assert context.dimension_domains[(1, 200)] == 10
    assert context.existing_terms[(1, 10, "人气")].target_id == 300
