from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from apps.semantic.models.orm import SemanticMetric, SemanticModel, SemanticModelMeasure


@dataclass(frozen=True, slots=True)
class MetricDependencyFacts:
    """指标依赖校验所需的已持久化事实。"""

    known_fields: frozenset[str] = frozenset()
    existing_metric_ids: frozenset[int] = frozenset()


class MetricRepository(Protocol):
    """指标生命周期应用服务依赖的持久化端口。"""

    def list_active(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticMetric]: ...

    def get_active(self, oid: int, metric_id: int) -> SemanticMetric | None: ...

    def list_model_measures(
        self,
        oid: int,
        model_id: int,
    ) -> list[SemanticModelMeasure]: ...

    def load_dependency_facts(
        self,
        metric: SemanticMetric,
    ) -> MetricDependencyFacts: ...

    def metric_is_referenced(self, oid: int, metric_id: int) -> bool: ...

    def invalidate_published_contracts_for_metric(
        self,
        oid: int,
        metric_id: int,
    ) -> None: ...

    def create(
        self,
        metric: SemanticMetric,
        model: SemanticModel,
    ) -> SemanticMetric: ...

    def create_many(
        self,
        metrics: list[SemanticMetric],
        model: SemanticModel,
    ) -> list[SemanticMetric]: ...

    def update(
        self,
        metric: SemanticMetric,
        affected_models: Sequence[SemanticModel],
    ) -> SemanticMetric: ...

    def delete(
        self,
        metric: SemanticMetric,
        model: SemanticModel | None,
    ) -> None: ...
