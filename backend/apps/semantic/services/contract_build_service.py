"""从物理表统一构建并保存完整语义契约。"""

from typing import Any

from apps.semantic.errors import (
    SemanticForbiddenError,
    SemanticNotFoundError,
    SemanticValidationError,
)
from apps.semantic.models.dto import (
    MetricContractBuildInput,
    SemanticContractBuildInput,
    SemanticContractBuildResult,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    SemanticDimension,
    SemanticModel,
)
from apps.semantic.repository.datasource_metadata_repository import (
    DatasourceMetadataRepository,
)
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.semantic_contract_repository import (
    PendingDimensionBinding,
    PendingMetricCapability,
    SemanticContractAssetBundle,
    SemanticContractRepository,
)
from apps.semantic.services.builders.metric_builder import (
    build_metrics_from_model_measures,
)
from apps.semantic.services.builders.model_builder import (
    SemanticModelBuilder,
    build_model_with_assets,
)


class SemanticContractBuildService:
    """负责新契约资产的校验、构建和一次事务入库。"""

    def __init__(
        self,
        repository: SemanticContractRepository,
        domain_reader: DomainRepository,
        datasource_reader: DatasourceMetadataRepository,
    ):
        self._repository = repository
        self._domain_reader = domain_reader
        self._datasource_reader = datasource_reader

    def build_from_physical(
        self,
        oid: int,
        payload: SemanticContractBuildInput,
    ) -> SemanticContractBuildResult:
        model_payload = payload.model
        if not self._domain_reader.is_active(oid, model_payload.domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")
        if not self._datasource_reader.is_accessible(oid, model_payload.datasource_id):
            raise SemanticForbiddenError("SEMANTIC_DATASOURCE_NOT_FOUND")

        effective_model_payload = model_payload
        if not model_payload.model_detail and model_payload.table_name:
            columns = self._datasource_reader.list_columns(
                model_payload.datasource_id,
                model_payload.table_name,
            )
            if not columns:
                raise SemanticValidationError("SEMANTIC_MODEL_COLUMNS_REQUIRED")
            model_detail = SemanticModelBuilder().build_table_schema(
                table_name=model_payload.table_name,
                columns=columns,
                source_type=model_payload.source_type,
                sql=model_payload.sql,
                datasource_id=model_payload.datasource_id,
            ).model_detail
            effective_model_payload = model_payload.model_copy(
                update={"model_detail": model_detail}
            )
        if not effective_model_payload.model_detail:
            raise SemanticValidationError("SEMANTIC_MODEL_COLUMNS_REQUIRED")

        # 新建资产只能以草稿入库，READY 必须由独立发布校验产生。
        effective_model_payload = effective_model_payload.model_copy(
            update={"contract_status": "DRAFT", "contract_version": None}
        )
        model_assets = build_model_with_assets(effective_model_payload, oid=oid)
        self._validate_model_contract(model_assets.model, model_assets.dimensions)

        entity_inputs = self._unique_by_reference(
            payload.business_entities,
            "SEMANTIC_BUSINESS_ENTITY_REFERENCE_DUPLICATED",
        )
        logical_inputs = self._unique_by_reference(
            payload.logical_dimensions,
            "SEMANTIC_LOGICAL_DIMENSION_REFERENCE_DUPLICATED",
        )
        for item in [*entity_inputs.values(), *logical_inputs.values()]:
            if item.domain_id != model_payload.domain_id:
                raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")
        existing_entity_names = {
            item.biz_name
            for item in self._repository.list_business_entities(oid)
            if item.domain_id == model_payload.domain_id
        }
        if existing_entity_names & {
            item.biz_name for item in entity_inputs.values()
        }:
            raise SemanticValidationError("SEMANTIC_BUSINESS_ENTITY_DUPLICATED")
        existing_logical_names = {
            item.biz_name
            for item in self._repository.list_logical_dimensions(
                oid, model_payload.domain_id
            )
        }
        if existing_logical_names & {
            item.biz_name for item in logical_inputs.values()
        }:
            raise SemanticValidationError("SEMANTIC_LOGICAL_DIMENSION_DUPLICATED")
        if len({item.biz_name for item in entity_inputs.values()}) != len(
            entity_inputs
        ):
            raise SemanticValidationError("SEMANTIC_BUSINESS_ENTITY_DUPLICATED")
        if len({item.biz_name for item in logical_inputs.values()}) != len(
            logical_inputs
        ):
            raise SemanticValidationError("SEMANTIC_LOGICAL_DIMENSION_DUPLICATED")

        entities = {
            reference: BusinessEntity(
                **item.model_dump(exclude={"reference"}),
                oid=oid,
            )
            for reference, item in entity_inputs.items()
        }
        logical_dimensions: dict[str, LogicalDimension] = {}
        logical_entity_references: dict[str, str] = {}
        for reference, item in logical_inputs.items():
            if item.entity_reference:
                if item.entity_reference not in entities:
                    raise SemanticValidationError(
                        "SEMANTIC_BUSINESS_ENTITY_REFERENCE_INVALID"
                    )
                logical_entity_references[reference] = item.entity_reference
            elif item.entity_id is not None:
                entity = self._repository.get_business_entity(oid, item.entity_id)
                if entity is None:
                    raise SemanticNotFoundError(
                        "SEMANTIC_BUSINESS_ENTITY_NOT_FOUND"
                    )
                if entity.domain_id != model_payload.domain_id:
                    raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")
            logical_dimensions[reference] = LogicalDimension(
                **item.model_dump(exclude={"reference", "entity_reference"}),
                oid=oid,
            )

        dimensions_by_biz_name = {
            item.biz_name: item for item in model_assets.dimensions
        }
        binding_inputs = self._unique_by_value(
            payload.dimension_bindings,
            "dimension_biz_name",
            "SEMANTIC_DIMENSION_BINDING_DUPLICATED",
        )
        if set(binding_inputs) != set(dimensions_by_biz_name):
            raise SemanticValidationError("SEMANTIC_DIMENSION_BINDING_INCOMPLETE")
        pending_bindings: list[PendingDimensionBinding] = []
        for dimension_biz_name, item in binding_inputs.items():
            self._validate_logical_dimension_reference(
                oid,
                model_payload.domain_id,
                item.logical_dimension_id,
                item.logical_dimension_reference,
                logical_dimensions,
            )
            pending_bindings.append(
                PendingDimensionBinding(
                    dimension_biz_name=dimension_biz_name,
                    logical_dimension_id=item.logical_dimension_id,
                    logical_dimension_reference=item.logical_dimension_reference,
                    binding_role=item.binding_role,
                    binding_priority=item.binding_priority,
                )
            )

        metric_inputs = self._unique_by_value(
            payload.metrics,
            "metric_biz_name",
            "SEMANTIC_METRIC_CONTRACT_DUPLICATED",
        )
        metric_result = build_metrics_from_model_measures(
            model_assets.model,
            oid,
            measure_biz_names=list(metric_inputs),
        )
        if metric_result.skipped:
            raise SemanticValidationError("SEMANTIC_METRIC_MEASURE_REFERENCE_INVALID")
        metrics_by_biz_name = {item.biz_name: item for item in metric_result.metrics}
        default_time_dimensions: dict[str, str] = {}
        for biz_name, item in metric_inputs.items():
            metric = metrics_by_biz_name[biz_name]
            metric.name = item.name or metric.name
            metric.description = item.description or metric.description
            metric.default_agg = item.default_agg
            metric.result_grain = item.result_grain
            metric.additivity = item.additivity
            metric.distinct_keys = item.distinct_keys
            metric.time_semantics = item.time_semantics
            metric.snapshot_aggregation = item.snapshot_aggregation
            metric.formula_definition = (
                item.formula_definition.model_dump(mode="json")
                if item.formula_definition is not None
                else {}
            )
            metric.comparison_grains = list(item.comparison_grains)
            metric.time_alignment_policy = item.time_alignment_policy
            self._validate_metric_contract(item, dimensions_by_biz_name)
            if item.default_time_dimension_biz_name:
                if (
                    binding_inputs[item.default_time_dimension_biz_name].binding_role
                    != "TIME"
                ):
                    raise SemanticValidationError(
                        "SEMANTIC_METRIC_TIME_DIMENSION_ROLE_INVALID"
                    )
                default_time_dimensions[biz_name] = (
                    item.default_time_dimension_biz_name
                )
        model_assets.metrics = metric_result.metrics

        capability_keys: set[tuple[str, str]] = set()
        pending_capabilities: list[PendingMetricCapability] = []
        for item in payload.capabilities:
            if item.metric_biz_name not in metrics_by_biz_name:
                raise SemanticValidationError("SEMANTIC_CAPABILITY_METRIC_INVALID")
            if item.physical_dimension_biz_name not in dimensions_by_biz_name:
                raise SemanticValidationError(
                    "SEMANTIC_CAPABILITY_PHYSICAL_DIMENSION_INVALID"
                )
            self._validate_logical_dimension_reference(
                oid,
                model_payload.domain_id,
                item.logical_dimension_id,
                item.logical_dimension_reference,
                logical_dimensions,
            )
            logical_key = item.logical_dimension_reference or str(
                item.logical_dimension_id
            )
            capability_key = (item.metric_biz_name, logical_key)
            if capability_key in capability_keys:
                raise SemanticValidationError("SEMANTIC_CAPABILITY_DUPLICATED")
            capability_keys.add(capability_key)
            binding = binding_inputs[item.physical_dimension_biz_name]
            if (
                binding.logical_dimension_id != item.logical_dimension_id
                or binding.logical_dimension_reference
                != item.logical_dimension_reference
            ):
                raise SemanticValidationError(
                    "SEMANTIC_CAPABILITY_DIMENSION_BINDING_MISMATCH"
                )
            pending_capabilities.append(
                PendingMetricCapability(
                    metric_biz_name=item.metric_biz_name,
                    logical_dimension_id=item.logical_dimension_id,
                    logical_dimension_reference=item.logical_dimension_reference,
                    physical_dimension_biz_name=item.physical_dimension_biz_name,
                    usages=list(item.usages),
                    aggregation_safety=item.aggregation_safety,
                    pre_aggregation_grain=item.pre_aggregation_grain,
                    time_alignment_policy=item.time_alignment_policy,
                    contribution_tolerance=item.contribution_tolerance,
                )
            )

        stored = self._repository.create_contract_assets(
            SemanticContractAssetBundle(
                model_assets=model_assets,
                business_entities=entities,
                logical_dimensions=logical_dimensions,
                logical_dimension_entity_references=logical_entity_references,
                dimension_bindings=pending_bindings,
                metric_default_time_dimensions=default_time_dimensions,
                capabilities=pending_capabilities,
            )
        )
        model_id = stored.model_assets.model.id
        if model_id is None:
            raise RuntimeError("SEMANTIC_MODEL_NOT_PERSISTED")
        return SemanticContractBuildResult(
            model_id=model_id,
            dimension_ids={
                item.biz_name: item.id
                for item in stored.model_assets.dimensions
                if item.id is not None
            },
            metric_ids={
                item.biz_name: item.id
                for item in stored.model_assets.metrics
                if item.id is not None
            },
            business_entity_ids={
                reference: item.id
                for reference, item in stored.business_entities.items()
                if item.id is not None
            },
            logical_dimension_ids={
                reference: item.id
                for reference, item in stored.logical_dimensions.items()
                if item.id is not None
            },
            capability_ids=[item.id for item in stored.capabilities if item.id is not None],
        )

    def _validate_model_contract(
        self,
        model: SemanticModel,
        dimensions: list[SemanticDimension],
    ) -> None:
        if model.model_kind not in {
            "ENTITY", "FACT", "DETAIL", "SNAPSHOT", "BRIDGE"
        }:
            raise SemanticValidationError("SEMANTIC_MODEL_KIND_INVALID")
        if not model.row_description:
            raise SemanticValidationError("SEMANTIC_MODEL_ROW_DESCRIPTION_REQUIRED")
        if model.model_kind in {"FACT", "DETAIL", "SNAPSHOT"} and not model.model_grain:
            raise SemanticValidationError("SEMANTIC_MODEL_GRAIN_REQUIRED")
        if model.model_kind == "ENTITY" and not model.primary_key:
            raise SemanticValidationError("SEMANTIC_MODEL_PRIMARY_KEY_REQUIRED")
        if model.model_kind == "SNAPSHOT" and not model.snapshot_time_field:
            raise SemanticValidationError("SEMANTIC_MODEL_SNAPSHOT_TIME_REQUIRED")
        detail = model.model_detail or {}
        field_names = {
            str(item.get("fieldName") or item.get("bizName") or "").strip()
            for item in detail.get("fields", [])
            if isinstance(item, dict)
        }
        references = [
            *model.primary_key,
            *model.model_grain,
            model.default_time_field,
            model.event_time_field,
            model.snapshot_time_field,
        ]
        if any(item and item not in field_names for item in references):
            raise SemanticValidationError("SEMANTIC_MODEL_FIELD_REFERENCE_INVALID")
        if model.default_time_field and model.default_time_field not in {
            name
            for dimension in dimensions
            for name in [dimension.biz_name, dimension.field_name]
            if name
        }:
            raise SemanticValidationError(
                "SEMANTIC_MODEL_DEFAULT_TIME_DIMENSION_INVALID"
            )

    def _validate_metric_contract(
        self,
        item: MetricContractBuildInput,
        dimensions_by_biz_name: dict[str, SemanticDimension],
    ) -> None:
        if not item.result_grain:
            raise SemanticValidationError("SEMANTIC_METRIC_GRAIN_REQUIRED")
        if any(
            field_name not in dimensions_by_biz_name
            for field_name in [*item.result_grain, *item.distinct_keys]
        ):
            raise SemanticValidationError("SEMANTIC_METRIC_FIELD_REFERENCE_INVALID")
        if item.default_agg == "COUNT_DISTINCT" and not item.distinct_keys:
            raise SemanticValidationError("SEMANTIC_METRIC_DISTINCT_KEYS_REQUIRED")
        has_time = item.time_semantics != "NONE"
        if has_time != bool(item.default_time_dimension_biz_name):
            raise SemanticValidationError("SEMANTIC_METRIC_TIME_DIMENSION_INVALID")
        if (
            item.default_time_dimension_biz_name
            and item.default_time_dimension_biz_name not in dimensions_by_biz_name
        ):
            raise SemanticValidationError("SEMANTIC_METRIC_TIME_DIMENSION_INVALID")
        is_snapshot = item.time_semantics in {"SNAPSHOT", "PERIODIC_SNAPSHOT"}
        if is_snapshot != bool(item.snapshot_aggregation):
            raise SemanticValidationError("SEMANTIC_METRIC_SNAPSHOT_POLICY_INVALID")

    def _validate_logical_dimension_reference(
        self,
        oid: int,
        domain_id: int,
        logical_dimension_id: int | None,
        logical_dimension_reference: str | None,
        local_dimensions: dict[str, LogicalDimension],
    ) -> None:
        if logical_dimension_reference:
            if logical_dimension_reference not in local_dimensions:
                raise SemanticValidationError(
                    "SEMANTIC_LOGICAL_DIMENSION_REFERENCE_INVALID"
                )
            return
        if logical_dimension_id is None:
            raise SemanticValidationError(
                "SEMANTIC_LOGICAL_DIMENSION_REFERENCE_REQUIRED"
            )
        logical_dimension = self._repository.get_logical_dimension(
            oid, logical_dimension_id
        )
        if logical_dimension is None:
            raise SemanticNotFoundError("SEMANTIC_LOGICAL_DIMENSION_NOT_FOUND")
        if logical_dimension.domain_id != domain_id:
            raise SemanticValidationError("SEMANTIC_CONTRACT_DOMAIN_MISMATCH")

    def _unique_by_reference(
        self, items: list[Any], error_code: str
    ) -> dict[str, Any]:
        return self._unique_by_value(items, "reference", error_code)

    def _unique_by_value(
        self,
        items: list[Any],
        field_name: str,
        error_code: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in items:
            value = getattr(item, field_name)
            if value in result:
                raise SemanticValidationError(error_code)
            result[value] = item
        return result


__all__ = ["SemanticContractBuildService"]
