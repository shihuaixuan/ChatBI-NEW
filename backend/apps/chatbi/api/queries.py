"""旧 `/chat` 下的生成与分析查询接口。"""

import traceback

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from apps.chatbi.api.legacy_chat_flow import LLMService
from apps.chatbi.api.legacy_read import (
    get_chat_record_by_id,
    list_recent_questions,
)
from apps.chatbi.api.legacy_sse import encode_sse_event
from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import ChatQuestion
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep
from common.interfaces.i18n import PLACEHOLDER_PREFIX

router = APIRouter(tags=["Data Q&A"], prefix="/chat")


@router.post(
    "/recommend_questions/{chat_record_id}",
    summary=f"{PLACEHOLDER_PREFIX}ask_recommend_questions",
)
async def ask_recommend_questions(
    session: SessionDep,
    current_user: CurrentUser,
    chat_record_id: int,
    current_assistant: CurrentAssistant,
    articles_number: int | None = 4,
):
    def _return_empty():
        yield encode_sse_event("recommended_question", content="[]")

    try:
        record = get_chat_record_by_id(session, chat_record_id)

        if not record:
            return StreamingResponse(_return_empty(), media_type="text/event-stream")

        request_question = ChatQuestion(
            chat_id=record.chat_id,
            question=record.question if record.question else "",
        )

        llm_service = await LLMService.create(
            session,
            current_user,
            request_question,
            current_assistant,
            True,
        )
        llm_service.set_record(record)
        llm_service.set_articles_number(articles_number)
        llm_service.run_recommend_questions_task_async()
    except Exception as exc:
        traceback.print_exc()

        def _err(error: Exception):
            yield encode_sse_event("error", content=str(error))

        return StreamingResponse(_err(exc), media_type="text/event-stream")

    return StreamingResponse(
        llm_service.await_result(),
        media_type="text/event-stream",
    )


@router.get(
    "/recent_questions/{dataset_id}",
    response_model=list[str],
    summary=f"{PLACEHOLDER_PREFIX}get_recommend_questions",
)
async def recommend_questions(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int = Path(..., description=f"{PLACEHOLDER_PREFIX}dataset_id"),
):
    return list_recent_questions(
        session=session,
        current_user=current_user,
        dataset_id=dataset_id,
    )


@router.post(
    "/record/{chat_record_id}/{action_type}",
    summary=f"{PLACEHOLDER_PREFIX}analysis_or_predict",
)
async def analysis_or_predict_question(
    session: SessionDep,
    current_user: CurrentUser,
    current_assistant: CurrentAssistant,
    chat_record_id: int,
    action_type: str = Path(
        ...,
        description=f"{PLACEHOLDER_PREFIX}analysis_or_predict_action_type",
    ),
):
    return await analysis_or_predict(
        session,
        current_user,
        chat_record_id,
        action_type,
        current_assistant,
    )


async def analysis_or_predict(
    session: SessionDep,
    current_user: CurrentUser,
    chat_record_id: int,
    action_type: str,
    current_assistant: CurrentAssistant,
    in_chat: bool = True,
    stream: bool = True,
):
    try:
        if action_type != "analysis" and action_type != "predict":
            raise Exception(f"Type {action_type} Not Found")
        record = build_chat_record_service(session).get_owned(
            current_user.id,
            chat_record_id,
        )

        if not record.chart:
            raise Exception(
                f"Chat record with id {chat_record_id} has not generated chart, "
                "do not support to analyze it"
            )

        request_question = ChatQuestion(
            chat_id=record.chat_id,
            question=record.question,
        )

        llm_service = await LLMService.create(
            session,
            current_user,
            request_question,
            current_assistant,
        )
        llm_service.run_analysis_or_predict_task_async(
            session,
            action_type,
            record,
            in_chat,
            stream,
        )
    except Exception as exc:
        traceback.print_exc()
        if stream:

            def _err(error: Exception):
                if in_chat:
                    yield encode_sse_event("error", content=str(error))
                else:
                    yield "&#x274c; **ERROR:**\n"
                    yield f"> {error}\n"

            return StreamingResponse(_err(exc), media_type="text/event-stream")
        return JSONResponse(
            content={"message": str(exc)},
            status_code=500,
        )
    if stream:
        return StreamingResponse(
            llm_service.await_result(),
            media_type="text/event-stream",
        )

    raw_data = {}
    for chunk in llm_service.await_result():
        if chunk:
            raw_data = chunk
    status_code = 200 if raw_data.get("success") else 500
    return JSONResponse(
        content=raw_data,
        status_code=status_code,
    )
