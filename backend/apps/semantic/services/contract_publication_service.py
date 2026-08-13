"""语义契约发布前的统一校验服务。"""

import hashlib
import json
from collections.abc import Iterable

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import (
    SemanticContractCompletenessReport,
    SemanticValidationCheck,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelRelation,
)
from apps.semantic.repository.schema_repository import (
    DatasetSchemaAssets,
    SchemaRepository,
)
from apps.semantic.repository.semantic_contract_repository import (
    SemanticContractRepository,
)
from apps.semantic.services.rules.semantic_contract import validate_semantic_contracts


class SemanticContractPublicationService:
    """对一组语义资产生成可追溯的发布完整度报告。"""

    def __init__(self, repository: SemanticContractRepository | None = None) -> None:
        self._repository = repository

    def validate_dataset(
        self,
        oid: int,
        dataset_id: int,
        schema_repository: SchemaRepository,
    ) -> SemanticContractCompletenessReport:
        """读取数据集快照并生成契约完整度报告。"""

        assets = _load_assets(schema_repository, oid, dataset_id)
        return self.validate(
            models=assets.models,
            metrics=assets.metrics,
            dimensions=assets.dimensions,
            relations=assets.model_relations,
            entities=assets.business_entities,
            logical_dimensions=assets.logical_dimensions,
            capabilities=assets.metric_dimension_capabilities,
            model_fields=assets.model_fields,
        )

    def publish_dataset(
        self,
        oid: int,
        dataset_id: int,
        schema_repository: SchemaRepository,
    ) -> SemanticContractCompletenessReport:
        """校验并发布数据集契约，失败时只返回报告而不写入。"""

        if self._repository is None:
            raise RuntimeError("SEMANTIC_CONTRACT_REPOSITORY_REQUIRED")
        assets = _load_assets(schema_repository, oid, dataset_id)
        report = self.validate(
            models=assets.models,
            metrics=assets.metrics,
            dimensions=assets.dimensions,
            relations=assets.model_relations,
            entities=assets.business_entities,
            logical_dimensions=assets.logical_dimensions,
            capabilities=assets.metric_dimension_capabilities,
            model_fields=assets.model_fields,
        )
        if report.status != "READY":
            return report
        contract_version = max(report.contract_version + 1, 1)
        self._repository.publish_contract(assets, contract_version)
        return report.model_copy(update={"contract_version": contract_version})

    def validate(
        self,
        *,
        models: list[SemanticModel],
        metrics: list[SemanticMetric],
        dimensions: list[SemanticDimension],
        relations: list[SemanticModelRelation],
        entities: list[BusinessEntity],
        logical_dimensions: list[LogicalDimension],
        capabilities: list[MetricDimensionCapability],
        model_fields: list[SemanticModelField] | None = None,
    ) -> SemanticContractCompletenessReport:
        checks = validate_semantic_contracts(
            models,
            metrics,
            dimensions,
            relations,
            entities,
            logical_dimensions,
            capabilities,
            model_fields or [],
        )
        reason_codes = list(dict.fromkeys(item.reason_code for item in checks if item.reason_code))
        status = "INVALID" if reason_codes else "READY"
        return SemanticContractCompletenessReport(
            status=status,
            checks=checks,
            reason_codes=reason_codes,
            contract_version=_contract_version(
                models,
                metrics,
                dimensions,
                relations,
                entities,
                logical_dimensions,
                capabilities,
            ),
            schema_fingerprint=_fingerprint(checks),
        )


def _contract_version(*groups: Iterable[object]) -> int:
    versions: list[int] = []
    for group in groups:
        versions.extend(
            int(getattr(item, "contract_version", None) or getattr(item, "version", 0) or 0)
            for item in group
        )
    return max(versions, default=0)


def _load_assets(
    repository: SchemaRepository,
    oid: int,
    dataset_id: int,
) -> DatasetSchemaAssets:
    try:
        return repository.load(oid, dataset_id)
    except ValueError as error:
        raise SemanticNotFoundError(str(error)) from error


def _fingerprint(checks: list[SemanticValidationCheck]) -> str:
    payload = [item.model_dump(mode="json") for item in checks]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
