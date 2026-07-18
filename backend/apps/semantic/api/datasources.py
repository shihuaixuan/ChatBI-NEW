from __future__ import annotations

from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.models.dto import (
    SemanticColumnMeta,
    SemanticTableMeta,
)
from apps.semantic.repository.datasource.metadata_repository import (
    SqlModelDatasourceMetadataRepository,
)
from apps.semantic.services.datasource_service import (
    SemanticDatasourceService,
)
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/datasources")
async def list_datasources(session: SessionDep, current_user: CurrentUser):
    with map_semantic_errors_to_http():
        return SemanticDatasourceService(
            SqlModelDatasourceMetadataRepository(session)
        ).list_datasources(
            current_user.oid
        )


@router.get(
    "/datasources/{datasource_id}/tables", response_model=list[SemanticTableMeta]
)
async def list_datasource_tables(
    session: SessionDep, current_user: CurrentUser, datasource_id: int
):
    with map_semantic_errors_to_http():
        return SemanticDatasourceService(
            SqlModelDatasourceMetadataRepository(session)
        ).list_datasource_tables(
            current_user.oid, datasource_id
        )


@router.get(
    "/datasources/{datasource_id}/tables/{table_name}/columns",
    response_model=list[SemanticColumnMeta],
)
async def list_datasource_columns(
    session: SessionDep, current_user: CurrentUser, datasource_id: int, table_name: str
):
    with map_semantic_errors_to_http():
        return SemanticDatasourceService(
            SqlModelDatasourceMetadataRepository(session)
        ).list_datasource_columns(
            current_user.oid, datasource_id, table_name
        )
