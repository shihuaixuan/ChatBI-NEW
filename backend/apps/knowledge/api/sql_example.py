"""SQL 示例库兼容 HTTP API。"""

# mypy: disable-error-code="untyped-decorator"

import asyncio
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.knowledge.composition import build_sql_example_service
from apps.knowledge.errors import SQLExampleError
from apps.knowledge.models.dto import (
    SQLExampleErrorDetail,
    SQLExampleInput,
    SQLExamplePage,
)
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentUser, SessionDep, Trans
from common.utils.excel import get_excel_column_count

router = APIRouter(tags=["SQL Examples"], prefix="/system/data-training")


@router.get(
    "/page/{current_page}/{page_size}",
    summary=f"{PLACEHOLDER_PREFIX}get_dt_page",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def page_sql_examples(
    session: SessionDep,
    current_user: CurrentUser,
    current_page: int,
    page_size: int,
    question: str | None = Query(None, description="搜索问题(可选)"),
) -> SQLExamplePage:
    return build_sql_example_service(session).page(
        current_user.oid,
        current_page,
        page_size,
        question,
    )


@router.put("", response_model=int, summary=f"{PLACEHOLDER_PREFIX}create_or_update_dt")
@require_permissions(
    permission=SqlbotPermission(
        role=["ws_admin"],
        type="ds",
        keyExpression="info.datasource",
    )
)
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE_OR_UPDATE,
        module=OperationModules.DATA_TRAINING,
        resource_id_expr="info.id",
        result_id_expr="result_self",
    )
)
async def create_or_update_sql_example(
    session: SessionDep,
    current_user: CurrentUser,
    trans: Trans,
    info: SQLExampleInput,
) -> int:
    service = build_sql_example_service(session)
    try:
        if info.id is not None:
            return service.update(current_user.oid, info)
        return service.create(current_user.oid, info)
    except SQLExampleError as exc:
        raise HTTPException(status_code=400, detail=_localize_error(trans, exc)) from exc


@router.delete("", summary=f"{PLACEHOLDER_PREFIX}delete_dt")
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.DATA_TRAINING,
        resource_id_expr="id_list",
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def delete_sql_examples(
    session: SessionDep,
    current_user: CurrentUser,
    id_list: list[int],
) -> None:
    build_sql_example_service(session).delete(current_user.oid, id_list)


@router.get("/{id}/enable/{enabled}", summary=f"{PLACEHOLDER_PREFIX}enable_dt")
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.DATA_TRAINING,
        resource_id_expr="id",
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def enable_sql_example(
    session: SessionDep,
    current_user: CurrentUser,
    id: int,
    enabled: bool,
    trans: Trans,
) -> None:
    try:
        build_sql_example_service(session).set_enabled(
            current_user.oid,
            id,
            enabled,
        )
    except SQLExampleError as exc:
        raise HTTPException(status_code=404, detail=_localize_error(trans, exc)) from exc


@router.get("/export", summary=f"{PLACEHOLDER_PREFIX}export_dt")
@system_log(
    LogConfig(
        operation_type=OperationType.EXPORT,
        module=OperationModules.DATA_TRAINING,
    )
)
async def export_sql_examples(
    trans: Trans,
    current_user: CurrentUser,
    question: str | None = Query(None, description="搜索问题(可选)"),
) -> StreamingResponse:
    def build_workbook() -> io.BytesIO:
        with Session(engine) as export_session:
            examples = build_sql_example_service(export_session).list_all(
                current_user.oid,
                question,
            )
        rows = [
            [
                item.question,
                item.description,
                item.datasource_name,
                item.advanced_application_name,
            ][: 4 if current_user.oid == 1 else 3]
            for item in examples
        ]
        dataframe = pd.DataFrame(
            rows,
            columns=_excel_columns(trans, current_user.oid == 1),
        )
        buffer = io.BytesIO()
        with pd.ExcelWriter(
            buffer,
            engine="xlsxwriter",
            engine_kwargs={"options": {"strings_to_numbers": False}},
        ) as writer:
            dataframe.to_excel(writer, sheet_name="Sheet1", index=False)
        buffer.seek(0)
        return buffer

    result = await asyncio.to_thread(build_workbook)
    return StreamingResponse(
        result,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/template", summary=f"{PLACEHOLDER_PREFIX}excel_template_dt")
async def sql_example_template(
    trans: Trans,
    current_user: CurrentUser,
) -> StreamingResponse:
    def build_workbook() -> io.BytesIO:
        row = [
            "查询TEST表内所有ID",
            "SELECT id FROM TEST",
            "生效数据源1",
            "生效高级应用名称",
        ][: 4 if current_user.oid == 1 else 3]
        dataframe = pd.DataFrame(
            [row],
            columns=_excel_columns(trans, current_user.oid == 1, template=True),
        )
        buffer = io.BytesIO()
        with pd.ExcelWriter(
            buffer,
            engine="xlsxwriter",
            engine_kwargs={"options": {"strings_to_numbers": False}},
        ) as writer:
            dataframe.to_excel(writer, sheet_name="Sheet1", index=False)
        buffer.seek(0)
        return buffer

    result = await asyncio.to_thread(build_workbook)
    return StreamingResponse(
        result,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/uploadExcel", summary=f"{PLACEHOLDER_PREFIX}upload_excel_dt")
@system_log(
    LogConfig(
        operation_type=OperationType.IMPORT,
        module=OperationModules.DATA_TRAINING,
    )
)
async def upload_sql_examples(
    trans: Trans,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> dict[str, int | str | None]:
    original_name = Path(file.filename or "")
    suffix = original_name.suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="Only support .xlsx/.xls")

    upload_directory = Path(settings.EXCEL_PATH).resolve()
    upload_directory.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]
    base_filename = f"{original_name.stem}_{token}"
    save_path = upload_directory / f"{base_filename}{suffix}"
    save_path.write_bytes(await file.read())
    try:
        return await asyncio.to_thread(
            _import_workbook,
            save_path,
            base_filename,
            current_user.oid,
            trans,
            upload_directory,
        )
    finally:
        save_path.unlink(missing_ok=True)


def _import_workbook(
    save_path: Path,
    base_filename: str,
    workspace_id: int,
    trans: Trans,
    upload_directory: Path,
) -> dict[str, int | str | None]:
    use_columns = [0, 1, 2, 3] if workspace_id == 1 else [0, 1, 2]
    requests: list[SQLExampleInput] = []
    for sheet_name in pd.ExcelFile(save_path).sheet_names:
        if get_excel_column_count(  # type: ignore[no-untyped-call]
            str(save_path), sheet_name
        ) < len(use_columns):
            raise ValueError(
                _translate(trans, "i18n_excel_import.col_num_not_match")
            )
        dataframe = pd.read_excel(
            save_path,
            sheet_name=sheet_name,
            engine="calamine",
            header=0,
            usecols=use_columns,
            dtype=str,
        ).fillna("")
        for _, row in dataframe.iterrows():
            question = str(row.iloc[0]).strip()
            description = str(row.iloc[1]).strip()
            datasource_name = str(row.iloc[2]).strip()
            assistant_name = (
                str(row.iloc[3]).strip() if workspace_id == 1 else ""
            )
            if not any([question, description, datasource_name, assistant_name]):
                continue
            requests.append(
                SQLExampleInput(
                    oid=workspace_id,
                    question=question,
                    description=description,
                    datasource_name=datasource_name,
                    advanced_application_name=assistant_name,
                )
            )

    with Session(engine) as session:
        result = build_sql_example_service(session).batch_import(
            workspace_id,
            requests,
        )

    error_excel_filename: str | None = None
    if result.failed_records:
        error_excel_filename = f"{base_filename}_error.xlsx"
        error_rows = [
            [
                failure.data.question,
                failure.data.description,
                failure.data.datasource_name,
                failure.data.advanced_application_name,
                [
                    _localize_detail(trans, detail)
                    for detail in failure.errors
                ],
            ][: 5 if workspace_id == 1 else 3]
            + ([] if workspace_id == 1 else [[
                _localize_detail(trans, detail)
                for detail in failure.errors
            ]])
            for failure in result.failed_records
        ]
        error_columns = _excel_columns(trans, workspace_id == 1)
        error_columns.append(_translate(trans, "i18n_data_training.error_info"))
        pd.DataFrame(error_rows, columns=error_columns).to_excel(
            upload_directory / error_excel_filename,
            index=False,
        )

    return {
        "success_count": result.success_count,
        "failed_count": len(result.failed_records),
        "duplicate_count": result.duplicate_count,
        "original_count": result.original_count,
        "error_excel_filename": error_excel_filename,
    }


def _excel_columns(
    trans: Any,
    include_assistant: bool,
    *,
    template: bool = False,
) -> list[str]:
    suffix = "_template" if template else ""
    columns = [
        _translate(trans, f"i18n_data_training.problem_description{suffix}"),
        _translate(trans, f"i18n_data_training.sample_sql{suffix}"),
        _translate(trans, f"i18n_data_training.effective_data_sources{suffix}"),
    ]
    if include_assistant:
        columns.append(
            _translate(trans, f"i18n_data_training.advanced_application{suffix}")
        )
    return columns


def _localize_error(trans: Any, error: SQLExampleError) -> str:
    message = _translate(trans, error.message_key)
    return message.format(*error.format_args) if error.format_args else message


def _localize_detail(trans: Any, detail: SQLExampleErrorDetail) -> str:
    message = _translate(trans, detail.message_key)
    return message.format(*detail.format_args) if detail.format_args else message


def _translate(trans: Any, key: str) -> str:
    return str(trans(key))
