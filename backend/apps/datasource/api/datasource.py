import asyncio
import hashlib
import io
import os
import uuid
from pathlib import Path as FilePath

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Path, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_metadata_service,
    build_datasource_service,
    build_excel_import_service,
)
from apps.datasource.database import get_schema
from apps.datasource.services import (
    DatasourceNameConflictError,
    DatasourceTableNotFoundError,
    ExcelImportFileError,
)
from common.interfaces.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.config import settings
from common.core.deps import CurrentUser, SessionDep, Trans
from common.utils.utils import SQLBotLogUtil

from ..crud.datasource import (
    check_status,
    check_status_by_id,
    clear_ws_ds_cache,
    fieldEnum,
    getTablesByDs,
    preview,
)
from ..models.dto import (
    ColumnSchemaResponse,
    CreateDatasource,
    DatasourceRecord,
    FieldObj,
    ImportRequest,
    PhysicalField,
    PhysicalTable,
    PreviewResponse,
    TableObj,
    TableSchemaResponse,
    UpdateDatasource,
)
from ..models.orm import CoreDatasource, CoreField, CoreTable
from ..utils.excel import parse_excel_preview

router = APIRouter(tags=["Datasource"], prefix="/datasource")
path = settings.EXCEL_PATH


@router.get("/ws/{oid}", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def query_by_oid(
    session: SessionDep, user: CurrentUser, oid: int
) -> list[DatasourceRecord]:
    current_oid = (
        oid
        if user.isAdmin and oid
        else (user.oid if user.oid is not None else 1)
    )
    return build_datasource_service(session).list_by_workspace(int(current_oid))


@router.get(
    "/list",
    response_model=list[DatasourceRecord],
    summary=f"{PLACEHOLDER_PREFIX}ds_list",
    description=f"{PLACEHOLDER_PREFIX}ds_list_description",
)
async def datasource_list(session: SessionDep, user: CurrentUser):
    workspace_id = user.oid if user.oid is not None else 1
    return build_datasource_service(session).list_by_workspace(workspace_id)


@router.post(
    "/get/{id}",
    response_model=DatasourceRecord,
    summary=f"{PLACEHOLDER_PREFIX}ds_get",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], keyExpression="id", type="ds")
)
async def get_datasource(
    session: SessionDep, id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id")
):
    return build_datasource_service(session).get(id)


@router.post("/check", response_model=bool, summary=f"{PLACEHOLDER_PREFIX}ds_check")
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def check(session: SessionDep, trans: Trans, ds: CoreDatasource):
    def inner():
        return check_status(session, trans, ds, True)

    return await asyncio.to_thread(inner)


@router.get(
    "/check/{ds_id}", response_model=bool, summary=f"{PLACEHOLDER_PREFIX}ds_check"
)
async def check_by_id(
    session: SessionDep,
    trans: Trans,
    ds_id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
):
    def inner():
        return check_status_by_id(session, trans, ds_id, True)

    return await asyncio.to_thread(inner)


@router.post(
    "/add", response_model=DatasourceRecord, summary=f"{PLACEHOLDER_PREFIX}ds_add"
)
@system_log(
    LogConfig(
        operation_type=OperationType.CREATE,
        module=OperationModules.DATASOURCE,
        result_id_expr="id",
    )
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def add(
    session: SessionDep, trans: Trans, user: CurrentUser, ds: CreateDatasource
):
    workspace_id = user.oid if user.oid is not None else 1
    service = build_datasource_service(session)
    try:
        result = await asyncio.to_thread(
            service.create,
            ds,
            actor_id=user.id,
            workspace_id=workspace_id,
        )
    except DatasourceNameConflictError as exc:
        raise HTTPException(
            status_code=500,
            detail=trans("i18n_ds_name_exist"),
        ) from exc
    await clear_ws_ds_cache(workspace_id)
    return result


@router.post(
    "/chooseTables/{id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}ds_choose_tables",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
async def choose_tables(
    session: SessionDep,
    trans: Trans,
    tables: list[PhysicalTable],
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
):
    service = build_datasource_metadata_service(session)
    try:
        await asyncio.to_thread(service.sync_selected_tables, id, tables)
    except DatasourceTableNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=trans("i18n_table_not_exist"),
        ) from exc


@router.post(
    "/update",
    response_model=DatasourceRecord,
    summary=f"{PLACEHOLDER_PREFIX}ds_update",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="ds.id")
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.DATASOURCE,
        resource_id_expr="ds.id",
    )
)
async def update(
    session: SessionDep, trans: Trans, user: CurrentUser, ds: UpdateDatasource
):
    workspace_id = user.oid if user.oid is not None else 1
    service = build_datasource_service(session)
    try:
        return await asyncio.to_thread(service.update, workspace_id, ds)
    except DatasourceNameConflictError as exc:
        raise HTTPException(
            status_code=500,
            detail=trans("i18n_ds_name_exist"),
        ) from exc


@router.post(
    "/delete/{id}/{name}", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_delete"
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
@system_log(
    LogConfig(
        operation_type=OperationType.DELETE,
        module=OperationModules.DATASOURCE,
        resource_id_expr="id",
    )
)
async def delete(
    session: SessionDep,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
    name: str = None,
):
    _ = name
    datasource = await asyncio.to_thread(build_datasource_service(session).delete, id)
    await clear_ws_ds_cache(datasource.oid)
    return {"message": f"Datasource with ID {id} deleted successfully."}


@router.post(
    "/getTables/{id}",
    response_model=list[TableSchemaResponse],
    summary=f"{PLACEHOLDER_PREFIX}ds_get_tables",
)
@require_permissions(permission=SqlbotPermission(type="ds", keyExpression="id"))
async def get_tables(
    session: SessionDep, id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id")
):
    service = build_datasource_connection_service(session)
    return await asyncio.to_thread(service.list_tables, id)


@router.post(
    "/getTablesByConf",
    response_model=list[TableSchemaResponse],
    summary=f"{PLACEHOLDER_PREFIX}ds_get_tables",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def get_tables_by_conf(session: SessionDep, trans: Trans, ds: CoreDatasource):
    try:

        def inner():
            return getTablesByDs(session, ds)

        return await asyncio.to_thread(inner)
    except Exception as e:
        # check ds status
        def inner():
            return check_status(session, trans, ds, True)

        status = await asyncio.to_thread(inner)
        if status:
            SQLBotLogUtil.error(f"get table failed: {e}")
            raise HTTPException(status_code=500, detail=f"Get table Failed: {e.args}")


@router.post(
    "/getSchemaByConf",
    response_model=list[str],
    summary=f"{PLACEHOLDER_PREFIX}ds_get_schema",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def get_schema_by_conf(session: SessionDep, trans: Trans, ds: CoreDatasource):
    try:

        def inner():
            return get_schema(ds)

        return await asyncio.to_thread(inner)
    except Exception as e:
        # check ds status
        def inner():
            return check_status(session, trans, ds, True)

        status = await asyncio.to_thread(inner)
        if status:
            SQLBotLogUtil.error(f"get table failed: {e}")
            raise HTTPException(status_code=500, detail=f"Get table Failed: {e.args}")


@router.post(
    "/getFields/{id}/{table_name}",
    response_model=list[ColumnSchemaResponse],
    summary=f"{PLACEHOLDER_PREFIX}ds_get_fields",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
async def get_fields(
    session: SessionDep,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
    table_name: str = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_table_name"),
):
    service = build_datasource_connection_service(session)
    return await asyncio.to_thread(service.list_fields, id, table_name)


@router.post(
    "/syncFields/{id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}ds_sync_fields",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def sync_fields(
    session: SessionDep,
    trans: Trans,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_table_id"),
):
    service = build_datasource_metadata_service(session)
    try:
        return await asyncio.to_thread(service.sync_table_fields, id)
    except DatasourceTableNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=trans("i18n_table_not_exist"),
        ) from exc


class TestObj(BaseModel):
    sql: str = None


# not used, just do test
""" @router.post("/execSql/{id}", include_in_schema=False)
async def exec_sql(session: SessionDep, id: int, obj: TestObj):
    def inner():
        data = execSql(session, id, obj.sql)
        try:
            data_obj = data.get('data')
            # print(orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS).decode())
            print(orjson.dumps(data_obj).decode())
        except Exception:
            traceback.print_exc()

        return data

    return await asyncio.to_thread(inner) """


@router.post(
    "/tableList/{id}",
    response_model=list[PhysicalTable],
    summary=f"{PLACEHOLDER_PREFIX}ds_table_list",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
async def table_list(
    session: SessionDep, id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id")
):
    return build_datasource_metadata_service(session).list_tables(id)


@router.post(
    "/fieldList/{id}",
    response_model=list[PhysicalField],
    summary=f"{PLACEHOLDER_PREFIX}ds_field_list",
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def field_list(
    session: SessionDep,
    field: FieldObj,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_table_id"),
):
    return build_datasource_metadata_service(session).list_fields(id, field.fieldName)


@router.post("/editLocalComment", include_in_schema=False)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def edit_local(session: SessionDep, data: TableObj):
    if data.table is None:
        raise HTTPException(status_code=400, detail="Table is required")
    build_datasource_metadata_service(session).update_table_and_fields(
        data.table,
        data.fields,
    )


@router.post(
    "/editTable", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_edit_table"
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def edit_table(session: SessionDep, table: PhysicalTable):
    build_datasource_metadata_service(session).update_table(table)


@router.post(
    "/editField", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_edit_field"
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def edit_field(session: SessionDep, field: PhysicalField):
    build_datasource_metadata_service(session).update_field(field)


@router.post(
    "/previewData/{id}",
    response_model=PreviewResponse,
    summary=f"{PLACEHOLDER_PREFIX}ds_preview_data",
)
@require_permissions(permission=SqlbotPermission(type="ds", keyExpression="id"))
async def preview_data(
    session: SessionDep,
    trans: Trans,
    current_user: CurrentUser,
    data: TableObj,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
):
    def inner():
        try:
            return preview(session, current_user, id, data)
        except Exception as e:
            ds = session.query(CoreDatasource).filter(CoreDatasource.id == id).first()
            # check ds status
            status = check_status(session, trans, ds, True)
            if status:
                SQLBotLogUtil.error(f"Preview failed: {e}")
                raise HTTPException(status_code=500, detail=f"Preview Failed: {e.args}")

    return await asyncio.to_thread(inner)


# not used
@router.post("/fieldEnum/{id}", include_in_schema=False)
async def field_enum(session: SessionDep, id: int):
    def inner():
        return fieldEnum(session, id)

    return await asyncio.to_thread(inner)


# @router.post("/uploadExcel")
# async def upload_excel(session: SessionDep, file: UploadFile = File(...)):
#     ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
#     if not file.filename.lower().endswith(tuple(ALLOWED_EXTENSIONS)):
#         raise HTTPException(400, "Only support .xlsx/.xls/.csv")
#
#     os.makedirs(path, exist_ok=True)
#     filename = f"{file.filename.split('.')[0]}_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]}.{file.filename.split('.')[1]}"
#     save_path = os.path.join(path, filename)
#     with open(save_path, "wb") as f:
#         f.write(await file.read())
#
#     def inner():
#         sheets = []
#         with get_data_engine() as conn:
#             if filename.endswith(".csv"):
#                 df = pd.read_csv(save_path, engine='c')
#                 tableName = f"sheet1_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]}"
#                 sheets.append({"tableName": tableName, "tableComment": ""})
#                 column_len = len(df.dtypes)
#                 fields = []
#                 for i in range(column_len):
#                     # build fields
#                     fields.append({"name": df.columns[i], "type": str(df.dtypes[i]), "relType": ""})
#                 # create table
#                 create_table(conn, tableName, fields)
#
#                 data = [
#                     {df.columns[i]: None if pd.isna(row[i]) else (int(row[i]) if "int" in str(df.dtypes[i]) else row[i])
#                      for i in range(len(row))}
#                     for row in df.values
#                 ]
#                 # insert data
#                 insert_data(conn, tableName, fields, data)
#             else:
#                 excel_engine = 'xlrd' if filename.endswith(".xls") else 'openpyxl'
#                 df_sheets = pd.read_excel(save_path, sheet_name=None, engine=excel_engine)
#                 # build columns and data to insert db
#                 for sheet_name, df in df_sheets.items():
#                     tableName = f"{sheet_name}_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]}"
#                     sheets.append({"tableName": tableName, "tableComment": ""})
#                     column_len = len(df.dtypes)
#                     fields = []
#                     for i in range(column_len):
#                         # build fields
#                         fields.append({"name": df.columns[i], "type": str(df.dtypes[i]), "relType": ""})
#                     # create table
#                     create_table(conn, tableName, fields)
#
#                     data = [
#                         {df.columns[i]: None if pd.isna(row[i]) else (
#                             int(row[i]) if "int" in str(df.dtypes[i]) else row[i])
#                          for i in range(len(row))}
#                         for row in df.values
#                     ]
#                     # insert data
#                     insert_data(conn, tableName, fields, data)
#
#         os.remove(save_path)
#         return {"filename": filename, "sheets": sheets}
#
#     return await asyncio.to_thread(inner)


# deprecated
@router.post(
    "/uploadExcel", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_upload_excel"
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def upload_excel(
    file: UploadFile = File(..., description=f"{PLACEHOLDER_PREFIX}ds_excel"),
):
    ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
    original_name = FilePath(file.filename or "")
    suffix = original_name.suffix.lower().lstrip(".")
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only support .xlsx/.xls/.csv")

    os.makedirs(path, exist_ok=True)
    filename = (
        f"{original_name.stem}_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]}"
        f".{suffix}"
    )
    save_path = FilePath(path) / filename
    save_path.write_bytes(await file.read())
    service = build_excel_import_service(path)
    try:
        return await asyncio.to_thread(service.import_all_file, filename)
    except ExcelImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


t_sheet = "数据表列表"
t_s_col = "Sheet名称"
t_n_col = "表名"
t_c_col = "表备注"
f_n_col = "字段名"
f_c_col = "字段备注"


@router.get(
    "/exportDsSchema/{id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}ds_export_ds_schema",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
async def export_ds_schema(
    session: SessionDep, id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id")
):
    # {
    #     'sheet':'', sheet name
    #     'c1_h':'', column1 column name
    #     'c2_h':'', column2 column name
    #     'c1':[], column1 data
    #     'c2':[], column2 data
    # }
    def inner():
        if id == 0:  # download template
            df_list = [
                {
                    "sheet": t_sheet,
                    "c0_h": t_s_col,
                    "c1_h": t_n_col,
                    "c2_h": t_c_col,
                    "c0": ["数据表1", "数据表2"],
                    "c1": ["user", "score"],
                    "c2": ["用来存放用户信息的数据表", "用来存放用户课程信息的数据表"],
                },
                {
                    "sheet": "数据表1",
                    "c1_h": f_n_col,
                    "c2_h": f_c_col,
                    "c1": ["id", "name"],
                    "c2": ["用户id", "用户姓名"],
                },
                {
                    "sheet": "数据表2",
                    "c1_h": f_n_col,
                    "c2_h": f_c_col,
                    "c1": ["course", "user_id", "score"],
                    "c2": ["课程名称", "用户ID", "课程得分"],
                },
            ]
        else:
            tables = (
                session.query(CoreTable)
                .filter(CoreTable.ds_id == id)
                .order_by(CoreTable.table_name.asc())
                .all()
            )
            if len(tables) == 0:
                raise HTTPException(400, "No tables")

            df_list = []
            df1 = {
                "sheet": t_sheet,
                "c0_h": t_s_col,
                "c1_h": t_n_col,
                "c2_h": t_c_col,
                "c0": [],
                "c1": [],
                "c2": [],
            }
            df_list.append(df1)
            for index, table in enumerate(tables):
                df1["c0"].append(f"Sheet{index}")
                df1["c1"].append(table.table_name)
                df1["c2"].append(table.custom_comment)

                fields = (
                    session.query(CoreField)
                    .filter(CoreField.table_id == table.id)
                    .order_by(CoreField.field_index.asc())
                    .all()
                )
                df_fields = {
                    "sheet": f"Sheet{index}",
                    "c1_h": f_n_col,
                    "c2_h": f_c_col,
                    "c1": [],
                    "c2": [],
                }
                for field in fields:
                    df_fields["c1"].append(field.field_name)
                    df_fields["c2"].append(field.custom_comment)
                df_list.append(df_fields)

        # build dataframe and export
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            for index, df in enumerate(df_list):
                if index == 0:
                    pd.DataFrame(
                        {
                            df["c0_h"]: df["c0"],
                            df["c1_h"]: df["c1"],
                            df["c2_h"]: df["c2"],
                        }
                    ).to_excel(writer, sheet_name=df["sheet"], index=False)
                else:
                    pd.DataFrame({df["c1_h"]: df["c1"], df["c2_h"]: df["c2"]}).to_excel(
                        writer, sheet_name=df["sheet"], index=False
                    )

        output.seek(0)

        return io.BytesIO(output.getvalue())

    # headers = {
    #     'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"
    # }

    result = await asyncio.to_thread(inner)
    return StreamingResponse(
        result,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post(
    "/uploadDsSchema/{id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}ds_upload_ds_schema",
)
@require_permissions(
    permission=SqlbotPermission(role=["ws_admin"], type="ds", keyExpression="id")
)
async def upload_ds_schema(
    session: SessionDep,
    id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}ds_id"),
    file: UploadFile = File(...),
):
    ALLOWED_EXTENSIONS = {"xlsx", "xls"}
    if not file.filename.lower().endswith(tuple(ALLOWED_EXTENSIONS)):
        raise HTTPException(400, "Only support .xlsx/.xls")

    try:
        contents = await file.read()
        excel_file = io.BytesIO(contents)

        sheet_names = pd.ExcelFile(excel_file, engine="openpyxl").sheet_names

        excel_file.seek(0)

        field_sheets = []
        table_sheet = None  # []
        for sheet in sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet, engine="openpyxl").fillna(
                ""
            )
            if sheet == t_sheet:
                table_sheet = df.where(pd.notnull(df), None).to_dict(orient="records")
            else:
                field_sheets.append(
                    {
                        "sheet_name": sheet,
                        "data": df.where(pd.notnull(df), None).to_dict(
                            orient="records"
                        ),
                    }
                )

        # print(field_sheets)

        # sheet table mapping
        sheet_table_map = {}

        # get data and update
        # update table comment
        if table_sheet and len(table_sheet) > 0:
            for table in table_sheet:
                sheet_table_map[table[t_s_col]] = table[t_n_col]
                session.query(CoreTable).filter(
                    and_(CoreTable.ds_id == id, CoreTable.table_name == table[t_n_col])
                ).update({"custom_comment": table[t_c_col]})

        # update field comment
        if field_sheets and len(field_sheets) > 0:
            for fields in field_sheets:
                if len(fields["data"]) > 0:
                    # get table id
                    table_name = sheet_table_map.get(fields["sheet_name"])
                    table = (
                        session.query(CoreTable)
                        .filter(
                            and_(
                                CoreTable.ds_id == id,
                                CoreTable.table_name == table_name,
                            )
                        )
                        .first()
                    )
                    if table:
                        for field in fields["data"]:
                            session.query(CoreField).filter(
                                and_(
                                    CoreField.ds_id == id,
                                    CoreField.table_id == table.id,
                                    CoreField.field_name == field[f_n_col],
                                )
                            ).update({"custom_comment": field[f_c_col]})
        session.commit()

        return True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse Excel Failed: {str(e)}")


@router.post(
    "/parseExcel", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_parse_excel"
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def parse_excel(
    file: UploadFile = File(..., description=f"{PLACEHOLDER_PREFIX}ds_excel"),
):
    ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv"}
    original_name = FilePath(file.filename or "")
    suffix = original_name.suffix.lower().lstrip(".")
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Only support .xlsx/.xls/.csv")

    os.makedirs(path, exist_ok=True)
    filename = (
        f"{original_name.stem}_{hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]}"
        f".{suffix}"
    )
    save_path = FilePath(path) / filename
    save_path.write_bytes(await file.read())

    def inner():
        sheets_data = parse_excel_preview(str(save_path))
        return {"filePath": filename, "data": sheets_data}

    try:
        return await asyncio.to_thread(inner)
    except Exception:
        save_path.unlink(missing_ok=True)
        raise


@router.post(
    "/importToDb", response_model=None, summary=f"{PLACEHOLDER_PREFIX}ds_import_to_db"
)
@require_permissions(permission=SqlbotPermission(role=["ws_admin"]))
async def import_to_db(trans: Trans, import_req: ImportRequest):
    service = build_excel_import_service(path)
    try:
        return await asyncio.to_thread(service.import_file, import_req)
    except ExcelImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{trans('i18n_ds_upload_error')}: {exc}",
        ) from exc
