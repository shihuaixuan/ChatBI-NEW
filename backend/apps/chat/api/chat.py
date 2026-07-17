import asyncio
import io
import traceback
from typing import Optional, List

import orjson
import pandas as pd
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from starlette.responses import JSONResponse

from apps.chat.curd.chat import delete_chat_with_user, get_chart_data_with_user, get_chat_predict_data_with_user, \
    list_chats, get_chat_with_records, create_chat, get_chat_chart_data, get_chat_predict_data, get_chat_with_records_with_data, get_chat_record_by_id, \
    format_json_data, format_json_list_data, get_chart_config, list_recent_questions, rename_chat_with_user, get_chat_log_history, get_chart_data_with_user_live
from apps.chat.models.chat_model import AxisObj, Chat, ChatInfo, ChatQuestion, ChatRecord, CreateChat, RenameChat
from apps.chat.services.semantic_binding import DatasetBindingError
from apps.chat.task.llm import LLMService
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.core.deps import CurrentAssistant, SessionDep, CurrentUser, Trans
from common.utils.data_format import DataFormat
from common.audit.models.log_model import OperationType, OperationModules
from common.audit.schemas.logger_decorator import LogConfig, system_log

router = APIRouter(tags=["Data Q&A"], prefix="/chat")


@router.get("/list", response_model=List[Chat], summary=f"{PLACEHOLDER_PREFIX}get_chat_list")
async def chats(session: SessionDep, current_user: CurrentUser):
    return list_chats(session, current_user)


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
        return rename_chat_with_user(session=session, current_user=current_user, rename_object=chat)
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
    return delete_chat_with_user(session=session, current_user=current_user, chart_id=chart_id)


@router.post("/start", response_model=ChatInfo, summary=f"{PLACEHOLDER_PREFIX}start_chat")
@system_log(LogConfig(
    operation_type=OperationType.CREATE,
    module=OperationModules.CHAT,
    result_id_expr="id"
))
async def start_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat):
    try:
        return create_chat(session, current_user, create_chat_obj)
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
async def start_chat(session: SessionDep, current_user: CurrentUser, current_assistant: CurrentAssistant,
                     create_chat_obj: CreateChat = CreateChat(origin=2)):
    try:
        return create_chat(session, current_user, create_chat_obj, create_chat_obj and create_chat_obj.dataset_id,
                           current_assistant)
    except DatasetBindingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/recommend_questions/{chat_record_id}", summary=f"{PLACEHOLDER_PREFIX}ask_recommend_questions")
async def ask_recommend_questions(session: SessionDep, current_user: CurrentUser, chat_record_id: int,
                                  current_assistant: CurrentAssistant, articles_number: Optional[int] = 4):
    def _return_empty():
        yield 'data:' + orjson.dumps({'content': '[]', 'type': 'recommended_question'}).decode() + '\n\n'

    try:
        record = get_chat_record_by_id(session, chat_record_id)

        if not record:
            return StreamingResponse(_return_empty(), media_type="text/event-stream")

        request_question = ChatQuestion(chat_id=record.chat_id, question=record.question if record.question else '')

        llm_service = await LLMService.create(session, current_user, request_question, current_assistant, True)
        llm_service.set_record(record)
        llm_service.set_articles_number(articles_number)
        llm_service.run_recommend_questions_task_async()
    except Exception as e:
        traceback.print_exc()

        def _err(_e: Exception):
            yield 'data:' + orjson.dumps({'content': str(_e), 'type': 'error'}).decode() + '\n\n'

        return StreamingResponse(_err(e), media_type="text/event-stream")

    return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")


@router.get("/recent_questions/{dataset_id}", response_model=List[str],
            summary=f"{PLACEHOLDER_PREFIX}get_recommend_questions")
async def recommend_questions(session: SessionDep, current_user: CurrentUser,
                              dataset_id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}dataset_id")):
    return list_recent_questions(session=session, current_user=current_user, dataset_id=dataset_id)


@router.post("/record/{chat_record_id}/{action_type}", summary=f"{PLACEHOLDER_PREFIX}analysis_or_predict")
async def analysis_or_predict_question(session: SessionDep, current_user: CurrentUser,
                                       current_assistant: CurrentAssistant, chat_record_id: int,
                                       action_type: str = Path(...,
                                                               description=f"{PLACEHOLDER_PREFIX}analysis_or_predict_action_type")):
    return await analysis_or_predict(session, current_user, chat_record_id, action_type, current_assistant)


async def analysis_or_predict(session: SessionDep, current_user: CurrentUser, chat_record_id: int, action_type: str,
                              current_assistant: CurrentAssistant, in_chat: bool = True, stream: bool = True):
    try:
        if action_type != 'analysis' and action_type != 'predict':
            raise Exception(f"Type {action_type} Not Found")
        record: ChatRecord | None = None

        stmt = select(ChatRecord.id, ChatRecord.question, ChatRecord.chat_id, ChatRecord.datasource,
                      ChatRecord.engine_type,
                      ChatRecord.ai_modal_id, ChatRecord.create_by, ChatRecord.chart, ChatRecord.data).where(
            and_(ChatRecord.id == chat_record_id))
        result = session.execute(stmt)
        for r in result:
            record = ChatRecord(id=r.id, question=r.question, chat_id=r.chat_id, datasource=r.datasource,
                                engine_type=r.engine_type, ai_modal_id=r.ai_modal_id, create_by=r.create_by,
                                chart=r.chart,
                                data=r.data)

        if not record:
            raise Exception(f"Chat record with id {chat_record_id} not found")

        if not record.chart:
            raise Exception(
                f"Chat record with id {chat_record_id} has not generated chart, do not support to analyze it")

        request_question = ChatQuestion(chat_id=record.chat_id, question=record.question)

        llm_service = await LLMService.create(session, current_user, request_question, current_assistant)
        llm_service.run_analysis_or_predict_task_async(session, action_type, record, in_chat, stream)
    except Exception as e:
        traceback.print_exc()
        if stream:
            def _err(_e: Exception):
                if in_chat:
                    yield 'data:' + orjson.dumps({'content': str(_e), 'type': 'error'}).decode() + '\n\n'
                else:
                    yield f'&#x274c; **ERROR:**\n'
                    yield f'> {str(_e)}\n'

            return StreamingResponse(_err(e), media_type="text/event-stream")
        else:
            return JSONResponse(
                content={'message': str(e)},
                status_code=500,
            )
    if stream:
        return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")
    else:
        res = llm_service.await_result()
        raw_data = {}
        for chunk in res:
            if chunk:
                raw_data = chunk
        status_code = 200
        if not raw_data.get('success'):
            status_code = 500

        return JSONResponse(
            content=raw_data,
            status_code=status_code,
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
