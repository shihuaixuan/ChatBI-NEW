"""旧 `/chat` 下的生成与分析查询接口。"""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse
from sqlmodel import Session
from starlette.responses import JSONResponse

from apps.ai_model.runtime import build_llm_runtime
from apps.chatbi.adapters.auxiliary_generation import (
    build_auxiliary_generation_service,
)
from apps.chatbi.api.sse import encode_sse_event
from apps.chatbi.composition import (
    build_chat_record_service,
    build_history_query_service,
)
from apps.chatbi.models.dto.generation_runtime_settings import (
    GenerationRuntimeSettingsData,
)
from apps.chatbi.services.generation import resolve_runtime_settings
from apps.chatbi.services.generation.context.runtime_settings import (
    resolve_generation_language,
)
from apps.conversation import ChatRecordAuxiliaryType, ChatRecordNotFoundError
from apps.platform_config.composition import build_platform_parameter_service
from common.core.db import engine
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
    def _return_empty() -> Iterator[str]:
        yield encode_sse_event("recommended_question", content="[]")

    try:
        try:
            record = build_chat_record_service(session).get_owned(
                current_user.id,
                chat_record_id,
            )
        except ChatRecordNotFoundError:
            record = None

        if not record:
            return StreamingResponse(_return_empty(), media_type="text/event-stream")

        model_id, model_name, assistant_name, llm = await _resolve_generation_runtime(
            session,
            current_user,
            current_assistant,
            no_reasoning=True,
        )
        language = resolve_generation_language(current_user.language)
        record_snapshot = _snapshot_record(record)
        resolved_articles = articles_number if articles_number is not None else 4

        def stream() -> Iterator[str]:
            with Session(engine) as stream_session:
                service = build_auxiliary_generation_service(stream_session, llm)
                yield from service.stream_recommended_questions_for_record(
                    record=record_snapshot,
                    current_user=current_user,
                    current_assistant=current_assistant,
                    articles_number=resolved_articles,
                    language=language,
                    assistant_name=assistant_name,
                    model_id=model_id,
                    model_name=model_name,
                )

        return StreamingResponse(stream(), media_type="text/event-stream")
    except Exception as exc:
        traceback.print_exc()

        def _err(error: Exception) -> Iterator[str]:
            yield encode_sse_event("error", content=str(error))

        return StreamingResponse(_err(exc), media_type="text/event-stream")


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
    return build_history_query_service(session).list_recent_questions_for_dataset(
        user_id=current_user.id,
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
        base_record = build_chat_record_service(session).get_owned(
            current_user.id,
            chat_record_id,
        )

        if not base_record.chart:
            raise Exception(
                f"Chat record with id {chat_record_id} has not generated chart, "
                "do not support to analyze it"
            )

        auxiliary_record = build_chat_record_service(session).create_auxiliary(
            base_record,
            ChatRecordAuxiliaryType(action_type),
        )
        session.commit()

        model_id, model_name, assistant_name, llm = await _resolve_generation_runtime(
            session,
            current_user,
            current_assistant,
            no_reasoning=False,
        )
        language = resolve_generation_language(current_user.language)
        workspace_id = current_user.oid if current_user.oid is not None else 1
        record_snapshot = _snapshot_record(auxiliary_record)

        def produce() -> Iterator[str | dict[str, Any]]:
            with Session(engine) as stream_session:
                service = build_auxiliary_generation_service(stream_session, llm)
                yield from service.stream_analysis_or_predict(
                    action_type=action_type,
                    record=record_snapshot,
                    language=language,
                    assistant_name=assistant_name,
                    workspace_id=workspace_id,
                    model_id=model_id,
                    model_name=model_name,
                    in_chat=in_chat,
                    stream=stream,
                )

        if stream:
            return StreamingResponse(produce(), media_type="text/event-stream")

        raw_data: dict[str, Any] = {}
        for chunk in produce():
            if chunk:
                raw_data = chunk  # type: ignore[assignment]
        status_code = 200 if raw_data.get("success") else 500
        return JSONResponse(
            content=raw_data,
            status_code=status_code,
        )
    except Exception as exc:
        traceback.print_exc()
        if stream:

            def _err(error: Exception) -> Iterator[str]:
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


async def _resolve_generation_runtime(
    session: Session,
    current_user: CurrentUser,
    current_assistant: CurrentAssistant | None,
    *,
    no_reasoning: bool,
) -> tuple[int | None, str | None, str, Any]:
    specialized_model_id: str | int | None = None
    if current_assistant is not None and current_assistant.enable_custom_model:
        if current_assistant.custom_model:
            specialized_model_id = current_assistant.custom_model
    model_runtime = await build_llm_runtime(
        specialized_model_id,
        no_reasoning=no_reasoning,
    )
    parameter_values = await build_platform_parameter_service(session).list_group(
        "chat"
    )
    runtime_settings = resolve_runtime_settings(
        GenerationRuntimeSettingsData(
            assistant_name="Numora",
            enable_sql_row_limit=True,
            history_round_limit=0,
        ),
        parameter_values,
    )
    return (
        model_runtime.config.model_id,
        model_runtime.config.model_name,
        runtime_settings.assistant_name,
        model_runtime.llm,
    )


def _snapshot_record(record: Any) -> Any:
    """把请求期 Session 上的记录字段拷到独立快照，避免跨 Session 使用。"""

    from types import SimpleNamespace

    return SimpleNamespace(
        id=record.id,
        question=record.question,
        dataset_id=record.dataset_id,
        datasource=record.datasource,
        chat_id=record.chat_id,
    )


__all__ = [
    "analysis_or_predict",
    "router",
]
