"""Semantic 术语 Excel 接口。"""

from pathlib import Path
from secrets import token_hex

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.composition import build_semantic_term_excel_service
from common.core.config import settings
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")

_EXCEL_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_MAX_WORKBOOK_SIZE = 50 * 1024 * 1024


@router.get("/terms/export")
async def export_terms(
    session: SessionDep,
    current_user: CurrentUser,
    domain_id: int | None = None,
    word: str | None = None,
    dataset_ids: list[int] | None = Query(None),
) -> Response:
    with map_semantic_errors_to_http():
        content = build_semantic_term_excel_service(session).export_workbook(
            current_user.oid,
            domain_id=domain_id,
            word=word,
            dataset_ids=dataset_ids,
        )
    return _workbook_response(content, "semantic_terms.xlsx")


@router.get("/terms/template")
async def term_template(session: SessionDep) -> Response:
    content = build_semantic_term_excel_service(session).template_workbook()
    return _workbook_response(content, "semantic_terms_template.xlsx")


@router.post("/terms/upload-excel")
async def upload_terms(
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> dict[str, int | str | None]:
    filename = str(file.filename or "")
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=400,
            detail="SEMANTIC_TERM_EXCEL_FILE_TYPE_INVALID",
        )
    content = await file.read()
    if not content or len(content) > _MAX_WORKBOOK_SIZE:
        raise HTTPException(
            status_code=400,
            detail="SEMANTIC_TERM_EXCEL_FILE_SIZE_INVALID",
        )

    with map_semantic_errors_to_http():
        result = build_semantic_term_excel_service(session).import_workbook(
            current_user.oid,
            content,
        )
    error_filename = (
        _save_error_workbook(result.error_workbook)
        if result.error_workbook is not None
        else None
    )
    return {
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "duplicate_count": result.duplicate_count,
        "original_count": result.original_count,
        "error_excel_filename": error_filename,
    }


def _workbook_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=_EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _save_error_workbook(content: bytes) -> str:
    directory = Path(settings.EXCEL_PATH)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"semantic_terms_{token_hex(8)}_error.xlsx"
    (directory / filename).write_bytes(content)
    return filename
