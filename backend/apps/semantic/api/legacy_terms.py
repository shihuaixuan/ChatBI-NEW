"""旧术语 HTTP 路径到 Semantic 术语管理能力的兼容入口。"""

# mypy: disable-error-code="untyped-decorator"

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.composition import (
    build_legacy_terminology_compatibility_service,
)
from apps.semantic.models.dto import LegacyTerminologyDTO
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentUser, SessionDep
from common.interfaces.i18n import PLACEHOLDER_PREFIX

router = APIRouter(tags=["Terminology"], prefix="/system/terminology")


@router.get("/page/{current_page}/{page_size}", summary=f"{PLACEHOLDER_PREFIX}get_term_page")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def pager(
    session: SessionDep,
    current_user: CurrentUser,
    current_page: int,
    page_size: int,
    word: str | None = Query(None, description="搜索术语(可选)"),
    dslist: list[int] | None = Query(None, description="Semantic 数据集 ID 集合(可选)"),
    domain_id: int | None = Query(None, description="Semantic 主题域 ID(可选)"),
) -> dict[str, Any]:
    with map_semantic_errors_to_http():
        return build_legacy_terminology_compatibility_service(session).page_terms(
            current_user.oid,
            current_page=current_page,
            page_size=page_size,
            word=word,
            domain_id=domain_id,
            dataset_ids=dslist,
        )


@router.put("", summary=f"{PLACEHOLDER_PREFIX}create_or_update_term")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE_OR_UPDATE,
        module=OperationModules.TERMINOLOGY,
        resource_id_expr="info.id",
        result_id_expr="result_self",
    )
)
async def create_or_update(
    session: SessionDep,
    current_user: CurrentUser,
    info: LegacyTerminologyDTO,
) -> int:
    service = build_legacy_terminology_compatibility_service(session)
    with map_semantic_errors_to_http():
        if info.id is not None:
            return service.update_term(current_user.oid, info.id, info)
        return service.create_term(current_user.oid, info)


@router.delete("", summary=f"{PLACEHOLDER_PREFIX}delete_term")
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.TERMINOLOGY,
        resource_id_expr="id_list",
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def delete(
    session: SessionDep,
    current_user: CurrentUser,
    id_list: list[int],
) -> None:
    with map_semantic_errors_to_http():
        build_legacy_terminology_compatibility_service(session).delete_terms(
            current_user.oid,
            id_list,
        )


@router.get("/{id}/enable/{enabled}", summary=f"{PLACEHOLDER_PREFIX}enable_term")
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.TERMINOLOGY,
        resource_id_expr="id",
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def enable(
    session: SessionDep,
    current_user: CurrentUser,
    id: int,
    enabled: bool,
) -> None:
    with map_semantic_errors_to_http():
        build_legacy_terminology_compatibility_service(session).set_term_enabled(
            current_user.oid,
            id,
            enabled,
        )


@router.get("/export", summary=f"{PLACEHOLDER_PREFIX}export_term")
@system_log(
    LogConfig(
        operation_type=OperationType.EXPORT,
        module=OperationModules.TERMINOLOGY,
    )
)
async def export_excel() -> None:
    _raise_excel_contract_required()


@router.get("/template", summary=f"{PLACEHOLDER_PREFIX}excel_template_term")
async def excel_template() -> None:
    _raise_excel_contract_required()


@router.post("/uploadExcel", summary=f"{PLACEHOLDER_PREFIX}upload_term")
@system_log(
    LogConfig(
        operation_type=OperationType.IMPORT,
        module=OperationModules.TERMINOLOGY,
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def upload_excel(file: UploadFile = File(...)) -> None:
    _ = file
    _raise_excel_contract_required()


def _raise_excel_contract_required() -> None:
    """旧 Excel 缺少主题域和数据集列，禁止继续写入旧术语表。"""

    raise HTTPException(
        status_code=409,
        detail="SEMANTIC_TERM_EXCEL_CONTRACT_REQUIRED",
    )
