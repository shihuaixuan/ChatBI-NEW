import asyncio
import io

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from apps.chatbi.api.legacy_composition import build_legacy_conversation_service
from apps.chatbi.api.legacy_read import (
    format_json_data,
    format_json_list_data,
    get_chart_config,
    get_chart_data_with_user,
    get_chart_data_with_user_live,
    get_chat_chart_data,
    get_chat_log_history,
    get_chat_predict_data,
    get_chat_predict_data_with_user,
    get_chat_with_records,
    get_chat_with_records_with_data,
)
from apps.chatbi.composition import build_conversation_reader_service
from apps.chatbi.models import (
    Chat,
    ChatInfo,
    ChatRecord,
    CreateChat,
    RenameChat,
)
from apps.chatbi.services.conversation import DatasetBindingError
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep, Trans
from common.utils.data_format import DataFormat
from common.utils.data_format_schema import AxisObj

router = APIRouter(tags=["Data Q&A"], prefix="/chat")


@router.get("/list", response_model=list[Chat], summary=f"{PLACEHOLDER_PREFIX}get_chat_list")
async def chats(session: SessionDep, current_user: CurrentUser):
    return build_conversation_reader_service(session).list_for_owner(
        current_user.id,
        current_user.oid,
    )


@router.get("/{chart_id}", response_model=ChatInfo, summary=f"{PLACEHOLDER_PREFIX}get_chat")
async def get_chat(session: SessionDep, current_user: CurrentUser, chart_id: int, current_assistant: CurrentAssistant,
                   trans: Trans):
    def inner():
        return get_chat_with_records(chart_id=chart_id, session=session, current_user=current_user,
                                     current_assistant=current_assistant, trans=trans)

    return await asyncio.to_thread(inner)


@router.get("/{chart_id}/with_data", response_model=ChatInfo, summary=f"{PLACEHOLDER_PREFIX}get_chat_with_data")
async def get_chat_with_data(session: SessionDep, current_user: CurrentUser, chart_id: int,
                             current_assistant: CurrentAssistant):
    def inner():
        return get_chat_with_records_with_data(chart_id=chart_id, session=session, current_user=current_user,
                                               current_assistant=current_assistant)

    return await asyncio.to_thread(inner)


""" @router.get("/record/{chat_record_id}/data", summary=f"{PLACEHOLDER_PREFIX}get_chart_data")
async def chat_record_data(session: SessionDep, chat_record_id: int):
    def inner():
        data = get_chat_chart_data(chat_record_id=chat_record_id, session=session)
        return format_json_data(data)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/predict_data", summary=f"{PLACEHOLDER_PREFIX}get_chart_predict_data")
async def chat_predict_data(session: SessionDep, chat_record_id: int):
    def inner():
        data = get_chat_predict_data(chat_record_id=chat_record_id, session=session)
        return format_json_list_data(data)

    return await asyncio.to_thread(inner) """


@router.get("/record/{chat_record_id}/data", summary=f"{PLACEHOLDER_PREFIX}get_chart_data")
async def chat_record_data(session: SessionDep, current_user: CurrentUser, chat_record_id: int):
    def inner():
        data = get_chart_data_with_user(chat_record_id=chat_record_id, session=session, current_user=current_user)
        return format_json_data(data)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/data_live", summary=f"{PLACEHOLDER_PREFIX}get_chart_data_live")
async def chat_record_data_live(session: SessionDep, current_user: CurrentUser, chat_record_id: int):
    def inner():
        data = get_chart_data_with_user_live(chat_record_id=chat_record_id, session=session, current_user=current_user)
        return format_json_data(data)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/predict_data", summary=f"{PLACEHOLDER_PREFIX}get_chart_predict_data")
async def chat_predict_data(session: SessionDep, current_user: CurrentUser, chat_record_id: int):
    def inner():
        data = get_chat_predict_data_with_user(chat_record_id=chat_record_id, session=session,
                                               current_user=current_user)
        return format_json_list_data(data)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/log", summary=f"{PLACEHOLDER_PREFIX}get_record_log")
async def chat_record_log(session: SessionDep, current_user: CurrentUser, chat_record_id: int):
    def inner():
        return get_chat_log_history(session, chat_record_id, current_user)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/usage", summary=f"{PLACEHOLDER_PREFIX}get_record_usage")
async def chat_record_usage(session: SessionDep, current_user: CurrentUser, chat_record_id: int):
    def inner():
        return get_chat_log_history(session, chat_record_id, current_user, True)

    return await asyncio.to_thread(inner)


""" @router.post("/rename", response_model=str, summary=f"{PLACEHOLDER_PREFIX}rename_chat")
@system_log(LogConfig(
    operation_type=OperationType.UPDATE,
    module=OperationModules.CHAT,
    resource_id_expr="chat.id"
))
async def rename(session: SessionDep, chat: RenameChat):
    try:
        return rename_chat(session=session, rename_object=chat)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        ) """


@router.post("/rename", response_model=str, summary=f"{PLACEHOLDER_PREFIX}rename_chat")
@system_log(LogConfig(
    operation_type=OperationType.UPDATE,
    module=OperationModules.CHAT,
    resource_id_expr="chat.id"
))
async def rename(session: SessionDep, current_user: CurrentUser, chat: RenameChat):
    try:
        return build_conversation_reader_service(session).rename(current_user.id, chat)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


""" @router.delete("/{chart_id}/{brief}", response_model=str, summary=f"{PLACEHOLDER_PREFIX}delete_chat")
@system_log(LogConfig(
    operation_type=OperationType.DELETE,
    module=OperationModules.CHAT,
    resource_id_expr="chart_id",
    remark_expr="brief"
))
async def delete(session: SessionDep, chart_id: int, brief: str):
    try:
        return delete_chat(session=session, chart_id=chart_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        ) """


@router.delete("/{chart_id}/{brief}", response_model=str, summary=f"{PLACEHOLDER_PREFIX}delete_chat")
@system_log(LogConfig(
    operation_type=OperationType.DELETE,
    module=OperationModules.CHAT,
    resource_id_expr="chart_id",
    remark_expr="brief"
))
async def delete(session: SessionDep, current_user: CurrentUser, chart_id: int, brief: str):
    # 删除服务保留明确的权限、数据库冲突和 Artifact 清理错误，不在 API 层宽泛吞掉。
    return build_legacy_conversation_service(session).delete(current_user.id, chart_id)


@router.post("/start", response_model=ChatInfo, summary=f"{PLACEHOLDER_PREFIX}start_chat")
@system_log(LogConfig(
    operation_type=OperationType.CREATE,
    module=OperationModules.CHAT,
    result_id_expr="id"
))
async def start_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat):
    try:
        return build_legacy_conversation_service(session).create(
            user_id=current_user.id,
            workspace_id=current_user.oid,
            request=create_chat_obj,
        )
    except DatasetBindingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/assistant/start", response_model=ChatInfo, summary=f"{PLACEHOLDER_PREFIX}assistant_start_chat")
@system_log(LogConfig(
    operation_type=OperationType.CREATE,
    module=OperationModules.CHAT,
    result_id_expr="id"
))
async def assistant_start_chat(
    session: SessionDep,
    current_user: CurrentUser,
    current_assistant: CurrentAssistant,
    create_chat_obj: CreateChat = CreateChat(origin=2),
):
    try:
        return build_legacy_conversation_service(session).create(
            user_id=current_user.id,
            workspace_id=current_user.oid,
            request=create_chat_obj,
            require_dataset=bool(create_chat_obj.dataset_id),
            assistant_type=current_assistant.type if current_assistant else None,
        )
    except DatasetBindingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.get("/record/{chat_record_id}/excel/export/{chat_id}", summary=f"{PLACEHOLDER_PREFIX}export_chart_data")
@system_log(LogConfig(operation_type=OperationType.EXPORT, module=OperationModules.CHAT, resource_id_expr="chat_id", ))
async def export_excel(session: SessionDep, current_user: CurrentUser, chat_record_id: int, chat_id: int, trans: Trans):
    chat_record = session.get(ChatRecord, chat_record_id)
    if not chat_record:
        raise HTTPException(
            status_code=500,
            detail=f"ChatRecord with id {chat_record_id} not found"
        )
    if chat_record.create_by != current_user.id:
        raise HTTPException(
            status_code=500,
            detail=f"ChatRecord with id {chat_record_id} not Owned by the current user"
        )
    is_predict_data = chat_record.predict_record_id is not None

    _origin_data = format_json_data(get_chat_chart_data(chat_record_id=chat_record_id, session=session))

    _base_field = _origin_data.get('fields')
    _data = _origin_data.get('data')

    if not _data:
        raise HTTPException(
            status_code=500,
            detail=trans("i18n_excel_export.data_is_empty")
        )

    chart_info = get_chart_config(session, chat_record_id)

    _title = chart_info.get('title') if chart_info.get('title') else 'Excel'

    fields = []
    if chart_info.get('columns') and len(chart_info.get('columns')) > 0:
        for column in chart_info.get('columns'):
            fields.append(AxisObj(name=column.get('name'), value=column.get('value')))
    # 处理 axis
    if axis := chart_info.get('axis'):
        # 处理 x 轴
        if x_axis := axis.get('x'):
            if 'name' in x_axis or 'value' in x_axis:
                fields.append(AxisObj(name=x_axis.get('name'), value=x_axis.get('value')))

        # 处理 y 轴 - 兼容数组和对象格式
        if y_axis := axis.get('y'):
            if isinstance(y_axis, list):
                for column in y_axis:
                    if 'name' in column or 'value' in column:
                        fields.append(AxisObj(name=column.get('name'), value=column.get('value')))
            elif isinstance(y_axis, dict) and ('name' in y_axis or 'value' in y_axis):
                fields.append(AxisObj(name=y_axis.get('name'), value=y_axis.get('value')))

        # 处理 series
        if series := axis.get('series'):
            if 'name' in series or 'value' in series:
                fields.append(AxisObj(name=series.get('name'), value=series.get('value')))

    _predict_data = []
    if is_predict_data:
        _predict_data = format_json_list_data(get_chat_predict_data(chat_record_id=chat_record_id, session=session))

    def inner():

        data_list = DataFormat.convert_large_numbers_in_object_array(obj_array=_data + _predict_data,
                                                                     int_threshold=1e11)

        md_data, _fields_list = DataFormat.convert_object_array_for_pandas(fields, data_list)

        # data, _fields_list, col_formats = LLMService.format_pd_data(fields, _data + _predict_data)

        df = pd.DataFrame(md_data, columns=_fields_list)

        buffer = io.BytesIO()

        with pd.ExcelWriter(buffer, engine='xlsxwriter',
                            engine_kwargs={'options': {'strings_to_numbers': False}}) as writer:
            df.to_excel(writer, sheet_name='Sheet1', index=False)

            # 获取 xlsxwriter 的工作簿和工作表对象
            # workbook = writer.book
            # worksheet = writer.sheets['Sheet1']
            #
            # for col_idx, fmt_type in col_formats.items():
            #     if fmt_type == 'text':
            #         worksheet.set_column(col_idx, col_idx, None, workbook.add_format({'num_format': '@'}))
            #     elif fmt_type == 'number':
            #         worksheet.set_column(col_idx, col_idx, None, workbook.add_format({'num_format': '0'}))

        buffer.seek(0)
        return io.BytesIO(buffer.getvalue())

    result = await asyncio.to_thread(inner)
    return StreamingResponse(result, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
