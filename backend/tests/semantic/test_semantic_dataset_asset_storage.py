from apps.semantic.models import (
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
)
from apps.semantic.storage_sync import (
    build_dataset_detail_from_storage,
    dataset_assets_from_detail,
    dataset_model_configs_from_detail,
)


def test_dataset_detail_sync_extracts_model_configs_and_assets():
    dataset = SemanticDataset(
        id=40,
        oid=1,
        domain_id=1,
        name="客户数据集",
        biz_name="customer_ds",
        data_set_detail={
            "dataSetModelConfigs": [
                {"id": 9, "includesAll": False, "metrics": [1, 2], "dimensions": [3]},
                {"id": 10, "includesAll": True, "metrics": [], "dimensions": []},
            ]
        },
    )

    configs = dataset_model_configs_from_detail(dataset)
    assets = dataset_assets_from_detail(dataset)

    assert [(item.model_id, item.includes_all) for item in configs] == [(9, False), (10, True)]
    assert [(item.model_id, item.asset_type, item.asset_id) for item in assets] == [
        (9, "METRIC", 1),
        (9, "METRIC", 2),
        (9, "DIMENSION", 3),
    ]


def test_build_dataset_detail_from_storage_keeps_frontend_shape():
    dataset = SemanticDataset(id=40, oid=1, domain_id=1, name="客户数据集", biz_name="customer_ds")
    detail = build_dataset_detail_from_storage(
        dataset,
        configs=[
            SemanticDatasetModelConfig(oid=1, dataset_id=40, model_id=9, includes_all=False),
            SemanticDatasetModelConfig(oid=1, dataset_id=40, model_id=10, includes_all=True),
        ],
        assets=[
            SemanticDatasetAsset(oid=1, dataset_id=40, model_id=9, asset_type="METRIC", asset_id=1),
            SemanticDatasetAsset(oid=1, dataset_id=40, model_id=9, asset_type="DIMENSION", asset_id=3),
        ],
    )

    assert detail == {
        "dataSetModelConfigs": [
            {"id": 9, "includesAll": False, "metrics": [1], "dimensions": [3]},
            {"id": 10, "includesAll": True, "metrics": [], "dimensions": []},
        ]
    }
