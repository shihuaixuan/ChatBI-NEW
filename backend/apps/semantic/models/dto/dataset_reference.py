from pydantic import ConfigDict, Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class SemanticDatasetReference(SemanticBaseDTO):
    """供其他领域校验数据集及其可用资产的只读契约。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    datasource_ids: tuple[int, ...] = ()
    metric_ids: tuple[int, ...] = ()
    dimension_ids: tuple[int, ...] = ()


class SemanticDatasetExecutionBinding(SemanticBaseDTO):
    """数据集执行绑定：按默认模型与配置顺序解析出的执行数据源。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    dataset_name: str
    datasource_id: int = Field(gt=0)


class SemanticDatasetSummary(SemanticBaseDTO):
    """数据集的最小展示信息：仅供跨领域读取名称与存在性。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    name: str
