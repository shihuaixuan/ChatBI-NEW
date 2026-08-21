"""语义契约发布前的统一校验服务。"""

import hashlib
import json
from collections.abc import Iterable

from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import (
    DatasetSchema,
    SemanticContractCompletenessReport,
    SemanticValidationCheck,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    MetricRelationshipDimension,
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelField,
    SemanticModelRelation,
)
from apps.semantic.models.orm.contract_version import SemanticContractVersion
from apps.semantic.repository.schema_repository import (
    DatasetSchemaAssets,
    SchemaRepository,
)
from apps.semantic.repository.semantic_contract_repository import (
    SemanticContractRepository,
)
from apps.semantic.services.builders.schema_builder import SemanticSchemaBuilder
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
            hierarchies=assets.dimension_hierarchies,
            hierarchy_levels=assets.dimension_hierarchy_levels,
            metric_relationships=assets.metric_relationships,
            relationship_dimensions=assets.metric_relationship_dimensions,
            selected_hierarchy_ids=assets.selected_dimension_hierarchy_ids,
            selected_relationship_ids=assets.selected_metric_relationship_ids,
            dataset=assets.dataset,
        )

    def publish_dataset(
        self,
        oid: int,
        dataset_id: int,
        schema_repository: SchemaRepository,
        published_by: int | None = None,
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
            hierarchies=assets.dimension_hierarchies,
            hierarchy_levels=assets.dimension_hierarchy_levels,
            metric_relationships=assets.metric_relationships,
            relationship_dimensions=assets.metric_relationship_dimensions,
            selected_hierarchy_ids=assets.selected_dimension_hierarchy_ids,
            selected_relationship_ids=assets.selected_metric_relationship_ids,
            dataset=assets.dataset,
        )
        if report.status != "READY":
            return report
        published_schema_fingerprint = report.schema_fingerprint

        # 版本号必须由仓储在锁定数据集记录的事务内生成，避免并发发布得到相同版本。
        def build_snapshot(contract_version: int) -> tuple[str, dict[str, object]]:
            nonlocal published_schema_fingerprint
            _apply_published_contract(assets, contract_version)
            published_schema = SemanticSchemaBuilder().build(assets)
            published_schema_fingerprint = published_schema.schema_fingerprint
            return published_schema.schema_fingerprint, _contract_asset_snapshot(
                assets, published_schema
            )

        contract_version = self._repository.publish_contract(
            assets,
            published_by=published_by,
            build_snapshot=build_snapshot,
        )
        return report.model_copy(
            update={
                "contract_version": contract_version,
                "schema_fingerprint": published_schema_fingerprint,
            }
        )

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
        hierarchies: list[DimensionHierarchy] | None = None,
        hierarchy_levels: list[DimensionHierarchyLevel] | None = None,
        metric_relationships: list[MetricRelationship] | None = None,
        relationship_dimensions: list[MetricRelationshipDimension] | None = None,
        selected_hierarchy_ids: list[int] | None = None,
        selected_relationship_ids: list[int] | None = None,
        dataset: SemanticDataset | None = None,
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
            hierarchies or [],
            hierarchy_levels or [],
            metric_relationships or [],
            relationship_dimensions or [],
            dataset=dataset,
        )
        hierarchy_ids = {item.id for item in hierarchies or []}
        relationship_ids = {item.id for item in metric_relationships or []}
        for asset_id in selected_hierarchy_ids or []:
            if asset_id not in hierarchy_ids:
                checks.append(
                    SemanticValidationCheck(
                        check_type="DATASET_ANALYSIS_ASSET_REFERENCE",
                        status="FAIL",
                        subject_refs=[f"dimension_hierarchy:{asset_id}"],
                        reason_code="SEMANTIC_ANALYSIS_ASSET_NOT_FOUND",
                        message="数据集引用的维度层级不存在、已停用或不属于当前租户",
                    )
                )
        for asset_id in selected_relationship_ids or []:
            if asset_id not in relationship_ids:
                checks.append(
                    SemanticValidationCheck(
                        check_type="DATASET_ANALYSIS_ASSET_REFERENCE",
                        status="FAIL",
                        subject_refs=[f"metric_relationship:{asset_id}"],
                        reason_code="SEMANTIC_ANALYSIS_ASSET_NOT_FOUND",
                        message="数据集引用的指标关系不存在、已停用或不属于当前租户",
                    )
                )
        reason_codes = list(
            dict.fromkeys(item.reason_code for item in checks if item.reason_code)
        )
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
                hierarchies or [],
                metric_relationships or [],
            ),
            schema_fingerprint=_fingerprint(checks),
        )

    def analyze_dataset_impact(
        self,
        oid: int,
        dataset_id: int,
        schema_repository: SchemaRepository,
    ) -> dict[str, object]:
        """比较当前草稿与最近发布快照，并查找引用变更资产的数据集。"""

        if self._repository is None:
            raise RuntimeError("SEMANTIC_CONTRACT_REPOSITORY_REQUIRED")
        assets = _load_assets(schema_repository, oid, dataset_id)
        draft_schema = SemanticSchemaBuilder().build(assets)
        versions = self._repository.list_contract_versions(oid, dataset_id)
        latest = versions[0] if versions else None
        published_schema = (
            latest.asset_snapshot.get("schema") if latest is not None else None
        )
        published_versions = (
            published_schema.get("asset_versions", {})
            if isinstance(published_schema, dict)
            else {}
        )
        draft_versions = draft_schema.asset_versions
        changed_asset_refs = sorted(
            ref
            for ref in set(published_versions) | set(draft_versions)
            if published_versions.get(ref) != draft_versions.get(ref)
        )
        latest_by_dataset: dict[int, SemanticContractVersion] = {}
        for version in self._repository.list_workspace_contract_versions(oid):
            latest_by_dataset.setdefault(version.dataset_id, version)
        impacted_datasets: list[dict[str, object]] = []
        changed = set(changed_asset_refs)
        for impacted_dataset_id, version in latest_by_dataset.items():
            schema = version.asset_snapshot.get("schema")
            asset_versions = (
                schema.get("asset_versions", {}) if isinstance(schema, dict) else {}
            )
            matched_refs = sorted(changed & set(asset_versions))
            if matched_refs:
                impacted_datasets.append(
                    {
                        "dataset_id": impacted_dataset_id,
                        "contract_version": version.contract_version,
                        "matched_asset_refs": matched_refs,
                        "is_source_dataset": impacted_dataset_id == dataset_id,
                    }
                )
        return {
            "dataset_id": dataset_id,
            "published_contract_version": latest.contract_version if latest else 0,
            "changed_asset_refs": changed_asset_refs,
            "impact_count": len(impacted_datasets),
            "impacted_datasets": impacted_datasets,
            "requires_republish": bool(changed_asset_refs),
        }


def _contract_version(*groups: Iterable[object]) -> int:
    versions: list[int] = []
    for group in groups:
        versions.extend(
            int(
                getattr(item, "contract_version", None)
                or getattr(item, "version", 0)
                or 0
            )
            for item in group
        )
    return max(versions, default=0)


def _load_assets(
    repository: SchemaRepository,
    oid: int,
    dataset_id: int,
) -> DatasetSchemaAssets:
    try:
        return repository.load(oid, dataset_id, include_drafts=True)
    except ValueError as error:
        raise SemanticNotFoundError(str(error)) from error


def _fingerprint(checks: list[SemanticValidationCheck]) -> str:
    payload = [item.model_dump(mode="json") for item in checks]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _apply_published_contract(
    assets: DatasetSchemaAssets,
    contract_version: int,
) -> None:
    """在一个入口把已校验资产转换为待持久化的发布状态。"""

    assets.dataset.contract_version = contract_version
    for model in assets.models:
        model.contract_status = "READY"
        model.contract_version = contract_version
    for relation in assets.model_relations:
        relation.contract_status = "READY"
        relation.contract_version = contract_version
    for metric in assets.metrics:
        metric.contract_version = contract_version
    for dimension in assets.dimensions:
        dimension.contract_version = contract_version
    for hierarchy in assets.dimension_hierarchies:
        hierarchy.contract_status = "CERTIFIED"
        hierarchy.version = max(hierarchy.version, 1)
    for relationship in assets.metric_relationships:
        relationship.contract_status = "CERTIFIED"
        relationship.version = max(relationship.version, 1)


def _contract_asset_snapshot(
    assets: DatasetSchemaAssets,
    published_schema: DatasetSchema,
) -> dict[str, object]:
    """固定发布时实际 Schema 和资产引用，供审计、回放和影响分析读取。"""

    return {
        "assets": [
            {
                "asset_type": item.asset_type,
                "asset_id": item.asset_id,
                "model_id": item.model_id,
                "sort_order": item.sort_order,
            }
            for item in assets.dataset_assets
            if item.status == 1
        ],
        "model_ids": [
            item.model_id for item in assets.dataset_model_configs if item.status == 1
        ],
        "schema": published_schema.model_dump(mode="json"),
    }
