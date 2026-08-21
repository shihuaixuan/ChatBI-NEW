from fastapi import APIRouter
from sqlmodel import col, select

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.composition import (
    build_semantic_contract_build_service,
    build_semantic_contract_publication_service,
)
from apps.semantic.errors import SemanticNotFoundError
from apps.semantic.models.dto import (
    BusinessEntityPayload,
    DimensionHierarchyDTO,
    DimensionHierarchyPayload,
    LogicalDimensionPayload,
    MetricAnalysisCapabilities,
    MetricDimensionCapabilityPayload,
    MetricRelationshipDTO,
    MetricRelationshipPayload,
    SemanticContractBuildInput,
    SemanticContractBuildResult,
    SemanticContractCompletenessReport,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
)
from apps.semantic.models.orm.contract_version import SemanticContractVersion
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.repository.sqlmodel.semantic_contract_repository import (
    SqlModelSemanticContractRepository,
)
from apps.semantic.services.analysis_capabilities import build_analysis_capabilities
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.semantic.services.semantic_contract_service import SemanticContractService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


def _service(session: SessionDep) -> SemanticContractService:
    return SemanticContractService(
        SqlModelSemanticContractRepository(session),
        SqlModelDomainRepository(session),
    )


@router.post("/contracts/build-from-physical")
async def build_contract_from_physical(
    session: SessionDep,
    current_user: CurrentUser,
    payload: SemanticContractBuildInput,
) -> SemanticContractBuildResult:
    """从一个物理表构建完整语义资产并统一入库。"""

    with map_semantic_errors_to_http():
        return build_semantic_contract_build_service(session).build_from_physical(
            current_user.oid,
            payload,
        )


@router.get("/business-entities")
async def list_business_entities(
    session: SessionDep, current_user: CurrentUser
) -> list[BusinessEntity]:
    with map_semantic_errors_to_http():
        return _service(session).list_business_entities(current_user.oid)


@router.post("/business-entities")
async def create_business_entity(
    session: SessionDep,
    current_user: CurrentUser,
    payload: BusinessEntityPayload,
) -> BusinessEntity:
    with map_semantic_errors_to_http():
        return _service(session).create_business_entity(current_user.oid, payload)


@router.put("/business-entities/{entity_id}")
async def update_business_entity(
    session: SessionDep,
    current_user: CurrentUser,
    entity_id: int,
    payload: BusinessEntityPayload,
) -> BusinessEntity:
    with map_semantic_errors_to_http():
        return _service(session).update_business_entity(
            current_user.oid, entity_id, payload
        )


@router.delete("/business-entities/{entity_id}")
async def delete_business_entity(
    session: SessionDep, current_user: CurrentUser, entity_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_business_entity(current_user.oid, entity_id)


@router.get("/logical-dimensions")
async def list_logical_dimensions(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int | None = None,
) -> list[LogicalDimension]:
    with map_semantic_errors_to_http():
        return _service(session).list_logical_dimensions(current_user.oid, domain_id)


@router.post("/logical-dimensions")
async def create_logical_dimension(
    session: SessionDep,
    current_user: CurrentUser,
    payload: LogicalDimensionPayload,
) -> LogicalDimension:
    with map_semantic_errors_to_http():
        return _service(session).create_logical_dimension(current_user.oid, payload)


@router.put("/logical-dimensions/{dimension_id}")
async def update_logical_dimension(
    session: SessionDep,
    current_user: CurrentUser,
    dimension_id: int,
    payload: LogicalDimensionPayload,
) -> LogicalDimension:
    with map_semantic_errors_to_http():
        return _service(session).update_logical_dimension(
            current_user.oid, dimension_id, payload
        )


@router.delete("/logical-dimensions/{dimension_id}")
async def delete_logical_dimension(
    session: SessionDep, current_user: CurrentUser, dimension_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_logical_dimension(
            current_user.oid, dimension_id
        )


@router.get("/metric-dimension-capabilities")
async def list_metric_dimension_capabilities(
    session: SessionDep,
    current_user: CurrentUser,
    metric_id: int | None = None,
) -> list[MetricDimensionCapability]:
    with map_semantic_errors_to_http():
        return _service(session).list_metric_dimension_capabilities(
            current_user.oid, metric_id
        )


@router.post("/metric-dimension-capabilities")
async def create_metric_dimension_capability(
    session: SessionDep,
    current_user: CurrentUser,
    payload: MetricDimensionCapabilityPayload,
) -> MetricDimensionCapability:
    with map_semantic_errors_to_http():
        return _service(session).create_metric_dimension_capability(
            current_user.oid, payload
        )


@router.put("/metric-dimension-capabilities/{capability_id}")
async def update_metric_dimension_capability(
    session: SessionDep,
    current_user: CurrentUser,
    capability_id: int,
    payload: MetricDimensionCapabilityPayload,
) -> MetricDimensionCapability:
    with map_semantic_errors_to_http():
        return _service(session).update_metric_dimension_capability(
            current_user.oid, capability_id, payload
        )


@router.delete("/metric-dimension-capabilities/{capability_id}")
async def delete_metric_dimension_capability(
    session: SessionDep, current_user: CurrentUser, capability_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_metric_dimension_capability(
            current_user.oid, capability_id
        )


@router.get("/dimension-hierarchies", response_model=list[DimensionHierarchyDTO])
async def list_dimension_hierarchies(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int | None = None,
) -> list[DimensionHierarchyDTO]:
    with map_semantic_errors_to_http():
        return _service(session).list_dimension_hierarchies(current_user.oid, domain_id)


@router.post("/dimension-hierarchies", response_model=DimensionHierarchyDTO)
async def create_dimension_hierarchy(
    session: SessionDep,
    current_user: CurrentUser,
    payload: DimensionHierarchyPayload,
) -> DimensionHierarchyDTO:
    with map_semantic_errors_to_http():
        return _service(session).create_dimension_hierarchy(current_user.oid, payload)


@router.put(
    "/dimension-hierarchies/{hierarchy_id}", response_model=DimensionHierarchyDTO
)
async def update_dimension_hierarchy(
    session: SessionDep,
    current_user: CurrentUser,
    hierarchy_id: int,
    payload: DimensionHierarchyPayload,
) -> DimensionHierarchyDTO:
    with map_semantic_errors_to_http():
        return _service(session).update_dimension_hierarchy(
            current_user.oid, hierarchy_id, payload
        )


@router.delete("/dimension-hierarchies/{hierarchy_id}")
async def delete_dimension_hierarchy(
    session: SessionDep,
    current_user: CurrentUser,
    hierarchy_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_dimension_hierarchy(
            current_user.oid, hierarchy_id
        )


@router.get("/metric-relationships", response_model=list[MetricRelationshipDTO])
async def list_metric_relationships(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int | None = None,
) -> list[MetricRelationshipDTO]:
    with map_semantic_errors_to_http():
        return _service(session).list_metric_relationships(current_user.oid, domain_id)


@router.post("/metric-relationships", response_model=MetricRelationshipDTO)
async def create_metric_relationship(
    session: SessionDep,
    current_user: CurrentUser,
    payload: MetricRelationshipPayload,
) -> MetricRelationshipDTO:
    with map_semantic_errors_to_http():
        return _service(session).create_metric_relationship(current_user.oid, payload)


@router.put(
    "/metric-relationships/{relationship_id}", response_model=MetricRelationshipDTO
)
async def update_metric_relationship(
    session: SessionDep,
    current_user: CurrentUser,
    relationship_id: int,
    payload: MetricRelationshipPayload,
) -> MetricRelationshipDTO:
    with map_semantic_errors_to_http():
        return _service(session).update_metric_relationship(
            current_user.oid, relationship_id, payload
        )


@router.delete("/metric-relationships/{relationship_id}")
async def delete_metric_relationship(
    session: SessionDep,
    current_user: CurrentUser,
    relationship_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_metric_relationship(
            current_user.oid, relationship_id
        )


@router.get("/datasets/{dataset_id}/contract-report")
async def get_contract_report(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> SemanticContractCompletenessReport:
    with map_semantic_errors_to_http():
        return build_semantic_contract_publication_service().validate_dataset(
            current_user.oid,
            dataset_id,
            SemanticSchemaLoader(session),
        )


@router.post("/datasets/{dataset_id}/publish-contract")
async def publish_contract(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> SemanticContractCompletenessReport:
    with map_semantic_errors_to_http():
        return build_semantic_contract_publication_service(session).publish_dataset(
            current_user.oid,
            dataset_id,
            SemanticSchemaLoader(session),
            published_by=current_user.id,
        )


@router.get(
    "/datasets/{dataset_id}/analysis-capabilities",
    response_model=list[MetricAnalysisCapabilities],
)
async def get_analysis_capabilities(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> list[MetricAnalysisCapabilities]:
    with map_semantic_errors_to_http():
        schema = SemanticSchemaService(
            SemanticSchemaLoader(session)
        ).get_dataset_schema(current_user.oid, dataset_id)
        return build_analysis_capabilities(schema)


def _owned_dataset(session: SessionDep, oid: int, dataset_id: int) -> SemanticDataset:
    """统一校验治理查询只能读取当前租户的数据集。"""

    dataset = session.get(SemanticDataset, dataset_id)
    if dataset is None or dataset.oid != oid or dataset.status != 1:
        raise SemanticNotFoundError("SEMANTIC_DATASET_NOT_FOUND")
    return dataset


@router.get("/datasets/{dataset_id}/contract-references")
async def get_contract_references(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> dict[str, object]:
    """返回数据集当前绑定的契约资产引用。"""

    with map_semantic_errors_to_http():
        dataset = _owned_dataset(session, current_user.oid, dataset_id)
        assets = list(
            session.exec(
                select(SemanticDatasetAsset).where(
                    SemanticDatasetAsset.oid == current_user.oid,
                    SemanticDatasetAsset.dataset_id == dataset.id,
                    SemanticDatasetAsset.status == 1,
                )
            ).all()
        )
        model_configs = list(
            session.exec(
                select(SemanticDatasetModelConfig).where(
                    SemanticDatasetModelConfig.oid == current_user.oid,
                    SemanticDatasetModelConfig.dataset_id == dataset.id,
                    SemanticDatasetModelConfig.status == 1,
                )
            ).all()
        )
        if not assets and not any(config.includes_all for config in model_configs):
            raise ValueError("SEMANTIC_DATASET_ASSET_MISSING")
        return {
            "dataset_id": dataset_id,
            "schema_version": dataset.schema_version,
            "contract_version": dataset.contract_version,
            "assets": [
                {
                    "asset_type": item.asset_type,
                    "asset_id": item.asset_id,
                    "model_id": item.model_id,
                }
                for item in assets
            ],
            "model_configs": [
                {
                    "model_id": item.model_id,
                    "includes_all": item.includes_all,
                    "is_default": item.is_default,
                    "sort_order": item.sort_order,
                }
                for item in model_configs
            ],
        }


@router.get("/datasets/{dataset_id}/impact-analysis")
async def get_contract_impact_analysis(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> dict[str, object]:
    """返回契约变更影响范围，作为发布前的只读检查结果。"""

    with map_semantic_errors_to_http():
        return build_semantic_contract_publication_service(
            session
        ).analyze_dataset_impact(
            current_user.oid,
            dataset_id,
            SemanticSchemaLoader(session),
        )


@router.get("/datasets/{dataset_id}/contract-versions")
async def get_contract_versions(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
) -> dict[str, object]:
    """返回当前契约版本和真实持久化的发布历史。"""

    with map_semantic_errors_to_http():
        dataset = _owned_dataset(session, current_user.oid, dataset_id)
        history = list(
            session.exec(
                select(SemanticContractVersion)
                .where(
                    SemanticContractVersion.oid == current_user.oid,
                    SemanticContractVersion.dataset_id == dataset_id,
                )
                .order_by(col(SemanticContractVersion.contract_version).desc())
            ).all()
        )
        return {
            "dataset_id": dataset_id,
            "current": {
                "schema_version": dataset.schema_version,
                "contract_version": dataset.contract_version,
                "status": "PUBLISHED" if dataset.contract_version > 0 else "DRAFT",
            },
            "history": [
                {
                    "id": item.id,
                    "schema_version": item.schema_version,
                    "contract_version": item.contract_version,
                    "schema_fingerprint": item.schema_fingerprint,
                    "asset_snapshot": item.asset_snapshot,
                    "published_by": item.published_by,
                    "published_at": item.published_at,
                }
                for item in history
            ],
        }
