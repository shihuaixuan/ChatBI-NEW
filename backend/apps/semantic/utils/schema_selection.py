from typing import Any

from apps.semantic.models.dto import DatasetModelConfig
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticModel,
)


def configured_model_ids(
    dataset: SemanticDataset,
    storage_configs: list[SemanticDatasetModelConfig] | None = None,
) -> list[int]:
    """按数据集配置顺序返回需要加载的模型。"""

    return [
        config["id"]
        for config in runtime_dataset_configs(dataset, storage_configs)
        if config.get("id") is not None
    ]


def selected_model_domain_ids(models: list[SemanticModel]) -> list[int]:
    domain_ids: list[int] = []
    for model in models:
        if model.domain_id not in domain_ids:
            domain_ids.append(model.domain_id)
    return domain_ids


def dataset_model_configs(dataset: SemanticDataset) -> list[DatasetModelConfig]:
    raw_configs = (dataset.data_set_detail or {}).get("dataSetModelConfigs") or []
    return [DatasetModelConfig.model_validate(raw) for raw in raw_configs]


def runtime_dataset_configs(
    dataset: SemanticDataset,
    storage_configs: list[SemanticDatasetModelConfig] | None = None,
) -> list[dict[str, Any]]:
    active_configs = [item for item in storage_configs or [] if item.status == 1]
    if active_configs:
        return [
            {"id": item.model_id, "includes_all": item.includes_all}
            for item in sorted(active_configs, key=lambda config: config.sort_order)
        ]
    return [
        {"id": item.id, "includes_all": item.includes_all}
        for item in dataset_model_configs(dataset)
    ]
