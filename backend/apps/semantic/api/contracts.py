from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.composition import (
    build_semantic_contract_build_service,
    build_semantic_contract_publication_service,
)
from apps.semantic.models.dto import (
    BusinessEntityPayload,
    LogicalDimensionPayload,
    MetricDimensionCapabilityPayload,
    SemanticContractBuildInput,
    SemanticContractBuildResult,
    SemanticContractCompletenessReport,
)
from apps.semantic.models.orm import (
    BusinessEntity,
    LogicalDimension,
    MetricDimensionCapability,
)
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.repository.sqlmodel.semantic_contract_repository import (
    SqlModelSemanticContractRepository,
)
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
        return _service(session).update_business_entity(current_user.oid, entity_id, payload)


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
        return _service(session).update_logical_dimension(current_user.oid, dimension_id, payload)


@router.delete("/logical-dimensions/{dimension_id}")
async def delete_logical_dimension(
    session: SessionDep, current_user: CurrentUser, dimension_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _service(session).delete_logical_dimension(current_user.oid, dimension_id)


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
        return _service(session).create_metric_dimension_capability(current_user.oid, payload)


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
        )
