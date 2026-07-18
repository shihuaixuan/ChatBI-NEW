from dataclasses import dataclass
from typing import Protocol

from apps.semantic.models.orm import SemanticDimension, SemanticMetric, SemanticModel


@dataclass
class SemanticModelAssetBundle:
    """模型与同步创建资产的仓储传输对象。"""

    model: SemanticModel
    dimensions: list[SemanticDimension]
    metrics: list[SemanticMetric]


class ModelReader(Protocol):
    """跨聚合协作所需的模型只读端口。"""

    def get_active(self, oid: int, model_id: int) -> SemanticModel | None: ...


class ModelRepository(ModelReader, Protocol):
    """模型生命周期应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModel]: ...

    def create(self, model: SemanticModel) -> SemanticModel: ...

    def update(self, model: SemanticModel) -> SemanticModel: ...

    def delete(self, model: SemanticModel) -> None: ...

    def create_with_assets(
        self,
        bundle: SemanticModelAssetBundle,
    ) -> SemanticModelAssetBundle: ...
