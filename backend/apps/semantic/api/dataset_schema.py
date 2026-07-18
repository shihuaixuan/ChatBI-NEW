from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import SchemaMapInfo, SchemaMapRequest
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import (
    SemanticSchemaService,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/datasets/{dataset_id}/schema")
async def get_dataset_schema(
    session: SessionDep, current_user: CurrentUser, dataset_id: int
):
    with map_semantic_errors_to_http():
        return SemanticSchemaService(SemanticSchemaLoader(session)).get_dataset_schema(
            current_user.oid, dataset_id
        )


@router.get("/datasets/{dataset_id}/ontology")
async def get_dataset_ontology(
    session: SessionDep, current_user: CurrentUser, dataset_id: int
):
    with map_semantic_errors_to_http():
        return SemanticSchemaService(SemanticSchemaLoader(session)).get_dataset_ontology(
            current_user.oid, dataset_id
        )


@router.post("/schema/map", response_model=SchemaMapInfo)
async def map_schema(
    session: SessionDep, current_user: CurrentUser, payload: SchemaMapRequest
):
    with map_semantic_errors_to_http():
        return SemanticSchemaService(SemanticSchemaLoader(session)).map_schema(
            current_user.oid, payload
        )
