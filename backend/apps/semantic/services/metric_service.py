from __future__ import annotations

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import MetricBatchCreateFromMeasuresPayload, MetricPayload
from apps.semantic.models.orm import SemanticMetric, SemanticModel
from apps.semantic.repository.metric_repository import MetricRepository
from apps.semantic.repository.model_repository import ModelReader
from apps.semantic.services.builders.metric_builder import (
    build_metrics_from_model_measures,
    normalize_metric_storage_fields,
)
from apps.semantic.services.rules.metric_quality import validate_metric_dependencies
from apps.semantic.utils.model_update import assign_values


class SemanticMetricService:
    """指标生命周期及质量校验的应用服务。"""

    def __init__(
        self,
        repository: MetricRepository,
        model_reader: ModelReader,
    ):
        self._repository = repository
        self._model_reader = model_reader

    def list_metrics(
        self,
        oid: int,
        model_id: int | None = None,
    ) -> list[SemanticMetric]:
        return self._repository.list_active(oid, model_id)

    def create_metric(self, oid: int, payload: MetricPayload) -> SemanticMetric:
        model = self._require_model(oid, payload.model_id)
        metric = SemanticMetric(**payload.model_dump(), oid=oid)
        self._validate_metric(metric)
        return self._repository.create(metric, model)

    def batch_create_from_measures(
        self,
        oid: int,
        payload: MetricBatchCreateFromMeasuresPayload,
    ) -> dict[str, object]:
        model = self._require_model(oid, payload.model_id)
        result = build_metrics_from_model_measures(
            model=model,
            oid=oid,
            measure_ids=payload.measure_ids,
            measure_biz_names=payload.measure_biz_names,
            storage_measures=self._repository.list_model_measures(
                oid,
                payload.model_id,
            ),
            existing_metrics=self._repository.list_active(
                oid,
                payload.model_id,
            ),
        )
        for metric in result.metrics:
            self._validate_metric(metric)
        created = self._repository.create_many(result.metrics, model)
        return {"created": created, "skipped": result.skipped}

    def update_metric(
        self,
        oid: int,
        metric_id: int,
        payload: MetricPayload,
    ) -> SemanticMetric:
        metric = self._repository.get_active(oid, metric_id)
        if metric is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_NOT_FOUND")
        source_model = self._model_reader.get_active(oid, metric.model_id)
        target_model = self._require_model(oid, payload.model_id)
        assign_values(metric, payload.model_dump())
        metric.contract_version = None
        self._validate_metric(metric)
        # 更新后的口径必须重新审核并发布，旧版本不能继续进入严格运行时。
        self._repository.invalidate_published_contracts_for_metric(oid, metric_id)
        return self._repository.update(
            metric,
            [model for model in (source_model, target_model) if model is not None],
        )

    def delete_metric(self, oid: int, metric_id: int) -> dict[str, int | bool]:
        metric = self._repository.get_active(oid, metric_id)
        if metric is None:
            raise SemanticNotFoundError("SEMANTIC_METRIC_NOT_FOUND")
        if self._repository.metric_is_referenced(oid, metric_id):
            raise SemanticValidationError("SEMANTIC_METRIC_IN_USE")
        model = self._model_reader.get_active(oid, metric.model_id)
        self._repository.delete(metric, model)
        return {"id": metric_id, "deleted": True}

    def _validate_metric(self, metric: SemanticMetric) -> None:
        if metric.formula_definition:
            metric.define_type = "METRIC"
            components = metric.formula_definition.get("components")
            if isinstance(components, list):
                metric.metric_refs = [
                    item["metric_id"]
                    for item in components
                    if isinstance(item, dict) and isinstance(item.get("metric_id"), int)
                ]
        normalize_metric_storage_fields(metric)
        validate_metric_dependencies(
            metric,
            self._repository.load_dependency_facts(metric),
        )

    def _require_model(self, oid: int, model_id: int) -> SemanticModel:
        model = self._model_reader.get_active(oid, model_id)
        if model is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_NOT_FOUND")
        return model
