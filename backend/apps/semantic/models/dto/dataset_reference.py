from pydantic import ConfigDict, Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class SemanticDatasetReference(SemanticBaseDTO):
    """供其他领域校验数据集及其可用资产的只读契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    datasource_ids: tuple[int, ...] = ()
    metric_ids: tuple[int, ...] = ()
    dimension_ids: tuple[int, ...] = ()
