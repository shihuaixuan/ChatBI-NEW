from collections.abc import Sequence

from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)
from apps.semantic.repository.metric_repository import (
    MetricDependencyFacts,
    MetricRepository,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.repository.sqlmodel.storage_sync import (
    mark_model_schema_changed,
    sync_metric_relations,
)


class SqlModelMetricRepository(MetricRepository):
    """基于 SQLModel 的指标仓储实现。"""

    def __init__(self, session: Session):
        self._session = session

    def list_active(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticMetric]:
        statement = select(SemanticMetric).where(
            SemanticMetric.oid == oid,
            SemanticMetric.status == 1,
        )
        if model_id is not None:
            statement = statement.where(SemanticMetric.model_id == model_id)
        return all_results(
            self._session.exec(statement.order_by(col(SemanticMetric.id)))
        )

    def get_active(self, oid: int, metric_id: int) -> SemanticMetric | None:
        metric = self._session.get(SemanticMetric, metric_id)
        if metric is None or metric.oid != oid or metric.status != 1:
            return None
        return metric

    def list_model_measures(
        self,
        oid: int,
        model_id: int,
    ) -> list[SemanticModelMeasure]:
        return all_results(
            self._session.exec(
                select(SemanticModelMeasure).where(
                    SemanticModelMeasure.oid == oid,
                    SemanticModelMeasure.model_id == model_id,
                    SemanticModelMeasure.status == 1,
                )
            )
        )

    def load_dependency_facts(
        self,
        metric: SemanticMetric,
    ) -> MetricDependencyFacts:
        fields = all_results(
            self._session.exec(
                select(SemanticModelField).where(
                    SemanticModelField.oid == metric.oid,
                    SemanticModelField.model_id == metric.model_id,
                    SemanticModelField.status == 1,
                )
            )
        )
        existing_metric_ids = (
            all_results(
                self._session.exec(
                    select(SemanticMetric.id).where(
                        SemanticMetric.oid == metric.oid,
                        SemanticMetric.model_id == metric.model_id,
                        col(SemanticMetric.id).in_(metric.metric_refs),
                        SemanticMetric.status == 1,
                    )
                )
            )
            if metric.metric_refs
            else []
        )
        return MetricDependencyFacts(
            known_fields=frozenset(
                str(value)
                for field in fields
                for value in (field.biz_name, field.field_name, field.expr)
                if value
            ),
            existing_metric_ids=frozenset(existing_metric_ids),
        )

    def create(
        self,
        metric: SemanticMetric,
        model: SemanticModel,
    ) -> SemanticMetric:
        self._session.add(metric)
        self._session.flush()
        self._session.refresh(metric)
        sync_metric_relations(self._session, metric)
        mark_model_schema_changed(self._session, model)
        self._session.commit()
        self._session.refresh(metric)
        return metric

    def create_many(
        self,
        metrics: list[SemanticMetric],
        model: SemanticModel,
    ) -> list[SemanticMetric]:
        for metric in metrics:
            self._session.add(metric)
        self._session.flush()
        for metric in metrics:
            self._session.refresh(metric)
            sync_metric_relations(self._session, metric)
        if metrics:
            mark_model_schema_changed(self._session, model)
        self._session.commit()
        for metric in metrics:
            self._session.refresh(metric)
        return metrics

    def update(
        self,
        metric: SemanticMetric,
        affected_models: Sequence[SemanticModel],
    ) -> SemanticMetric:
        self._session.add(metric)
        sync_metric_relations(self._session, metric)
        self._mark_models(affected_models)
        self._session.commit()
        self._session.refresh(metric)
        return metric

    def delete(
        self,
        metric: SemanticMetric,
        model: SemanticModel | None,
    ) -> None:
        self._session.delete(metric)
        if model is not None:
            mark_model_schema_changed(self._session, model)
        self._session.commit()

    def _mark_models(self, models: Sequence[SemanticModel]) -> None:
        seen: set[int] = set()
        for model in models:
            key = model.id if model.id is not None else id(model)
            if key in seen:
                continue
            seen.add(key)
            mark_model_schema_changed(self._session, model)
