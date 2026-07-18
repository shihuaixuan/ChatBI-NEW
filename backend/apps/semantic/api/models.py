from fastapi import APIRouter
from sqlmodel import Session

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import (
    ModelBuildSchemaPayload,
    ModelBuildSchemaResult,
    ModelCreateWithAssetsPayload,
    ModelPayload,
    ModelRelationPayload,
)
from apps.semantic.models.orm import SemanticModel, SemanticModelRelation
from apps.semantic.repository.datasource.metadata_repository import (
    SqlModelDatasourceMetadataRepository,
)
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.model_relation_repository import (
    SqlModelModelRelationRepository,
)
from apps.semantic.repository.sqlmodel.model_repository import (
    SqlModelModelRepository,
)
from apps.semantic.services.model_relation_service import (
    SemanticModelRelationService,
)
from apps.semantic.services.model_service import SemanticModelService
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


def _model_service(session: Session) -> SemanticModelService:
    return SemanticModelService(
        SqlModelModelRepository(session),
        SqlModelDomainRepository(session),
        SqlModelDatasourceMetadataRepository(session),
    )


def _model_relation_service(
    session: Session,
) -> SemanticModelRelationService:
    return SemanticModelRelationService(
        SqlModelModelRelationRepository(session),
        SqlModelModelRepository(session),
    )


@router.get("/models")
async def list_models(
    session: SessionDep, current_user: CurrentUser, domain_id: int | None = None
) -> list[SemanticModel]:
    with map_semantic_errors_to_http():
        return _model_service(session).list_models(current_user.oid, domain_id)


@router.post("/models")
async def create_model(
    session: SessionDep, current_user: CurrentUser, payload: ModelPayload
) -> SemanticModel:
    with map_semantic_errors_to_http():
        return _model_service(session).create_model(current_user.oid, payload)


@router.put("/models/{model_id}")
async def update_model(
    session: SessionDep, current_user: CurrentUser, model_id: int, payload: ModelPayload
) -> SemanticModel:
    with map_semantic_errors_to_http():
        return _model_service(session).update_model(current_user.oid, model_id, payload)


@router.delete("/models/{model_id}")
async def delete_model(
    session: SessionDep,
    current_user: CurrentUser,
    model_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _model_service(session).delete_model(current_user.oid, model_id)


@router.post("/models/build-schema")
async def build_model_schema(
    session: SessionDep, current_user: CurrentUser, payload: ModelBuildSchemaPayload
) -> ModelBuildSchemaResult:
    with map_semantic_errors_to_http():
        return _model_service(session).build_model_schema(current_user.oid, payload)


@router.post("/models/create-with-assets")
async def create_model_with_assets(
    session: SessionDep,
    current_user: CurrentUser,
    payload: ModelCreateWithAssetsPayload,
) -> dict[str, object]:
    with map_semantic_errors_to_http():
        return _model_service(session).create_model_with_assets(
            current_user.oid, payload
        )


@router.get("/model-relations")
async def list_model_relations(
    session: SessionDep, current_user: CurrentUser, domain_id: int | None = None
) -> list[SemanticModelRelation]:
    with map_semantic_errors_to_http():
        return _model_relation_service(session).list_model_relations(
            current_user.oid, domain_id
        )


@router.post("/model-relations")
async def create_model_relation(
    session: SessionDep, current_user: CurrentUser, payload: ModelRelationPayload
) -> SemanticModelRelation:
    with map_semantic_errors_to_http():
        return _model_relation_service(session).create_model_relation(
            current_user.oid, payload
        )


@router.put("/model-relations/{relation_id}")
async def update_model_relation(
    session: SessionDep,
    current_user: CurrentUser,
    relation_id: int,
    payload: ModelRelationPayload,
) -> SemanticModelRelation:
    with map_semantic_errors_to_http():
        return _model_relation_service(session).update_model_relation(
            current_user.oid, relation_id, payload
        )


@router.delete("/model-relations/{relation_id}")
async def delete_model_relation(
    session: SessionDep, current_user: CurrentUser, relation_id: int
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return _model_relation_service(session).delete_model_relation(
            current_user.oid, relation_id
        )
