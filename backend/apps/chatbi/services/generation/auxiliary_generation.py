"""分析、预测与推荐问题的应用编排。

统一准备生成输入、调用 Generation Service、写步骤日志与记录终态，
并通过 SSE 事件字典输出，保持现有 /chat 契约。
"""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from typing import Any, Protocol

import orjson

from apps.chatbi.adapters.assistant_schema import (
    is_dynamic_assistant,
    load_assistant_schema,
)
from apps.chatbi.api.sse import build_role_prompt_log, encode_sse_event
from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    RecommendedQuestionGenerationData,
)
from apps.chatbi.services.generation.analysis_prediction import (
    AnalysisPredictionService,
)
from apps.chatbi.services.generation.context.knowledge import (
    GenerationContextService,
)
from apps.chatbi.services.generation.context.schema_context import (
    SchemaContextService,
)
from apps.chatbi.services.generation.recommended_questions import (
    RecommendedQuestionService,
)
from apps.conversation import (
    ChatLogService,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
    ChatRecordService,
    ChatRecordStatus,
    HistoryQueryService,
    OperationEnum,
    format_chart_fields,
)
from common.error import SingleMessageError
from common.utils.utils import extract_nested_json


class AuxiliaryRecordView(Protocol):
    """辅助生成只读取记录的最小字段，不依赖 Conversation ORM。"""

    id: int | None
    question: str | None
    dataset_id: int | None
    datasource: int | None


class AuxiliaryGenerationService:
    """编排推荐问题、分析与预测的生成与流式输出。"""

    def __init__(
        self,
        *,
        chat_record_service: ChatRecordService,
        chat_log_service: ChatLogService,
        history_query_service: HistoryQueryService,
        generation_context_service: GenerationContextService,
        schema_context_service: SchemaContextService,
        analysis_prediction_service: AnalysisPredictionService,
        recommended_question_service: RecommendedQuestionService,
    ) -> None:
        self._chat_record_service = chat_record_service
        self._chat_log_service = chat_log_service
        self._history_query_service = history_query_service
        self._generation_context_service = generation_context_service
        self._schema_context_service = schema_context_service
        self._analysis_prediction_service = analysis_prediction_service
        self._recommended_question_service = recommended_question_service

    def stream_recommended_questions_for_record(
        self,
        *,
        record: AuxiliaryRecordView,
        current_user: Any,
        current_assistant: Any | None,
        articles_number: int,
        language: str,
        assistant_name: str,
        model_id: int | None,
        model_name: str | None,
    ) -> Iterator[str]:
        """为指定记录准备 Schema 并生成推荐问题。"""

        data = self._build_recommended_question_data(
            record=record,
            current_user=current_user,
            current_assistant=current_assistant,
            articles_number=articles_number,
            language=language,
            assistant_name=assistant_name,
        )
        yield from self.stream_recommended_questions(
            data=data,
            model_id=model_id,
            model_name=model_name,
        )

    def stream_recommended_questions(
        self,
        *,
        data: RecommendedQuestionGenerationData,
        model_id: int | None,
        model_name: str | None,
    ) -> Iterator[str]:
        """生成推荐问题并输出 SSE 事件。"""

        try:
            messages = self._recommended_question_service.prepare(data)
            log = self._chat_log_service.start_log(
                ai_modal_id=model_id,
                ai_modal_name=model_name,
                operate=OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
                record_id=data.record_id,
                full_message=build_role_prompt_log(messages),
            )
            for event in self._recommended_question_service.generate(data, messages):
                if event.kind == "chunk":
                    yield encode_sse_event(
                        "recommended_question_result",
                        content=event.content,
                        reasoning_content=event.reasoning_content,
                    )
                    continue
                self._chat_log_service.end_log(
                    log,
                    build_role_prompt_log(messages, event.content),
                    reasoning_content=event.reasoning_content,
                    token_usage=event.token_usage,
                )
                if event.recommended_question is not None:
                    yield encode_sse_event(
                        "recommended_question",
                        content=event.recommended_question,
                    )
        except Exception:
            # 保持旧推荐问题接口静默失败契约：异常只记录堆栈，不中断空流结束。
            traceback.print_exc()

    def stream_analysis_or_predict(
        self,
        *,
        action_type: str,
        record: AuxiliaryRecordView,
        language: str,
        assistant_name: str,
        workspace_id: int,
        model_id: int | None,
        model_name: str | None,
        in_chat: bool = True,
        stream: bool = True,
    ) -> Iterator[str | dict[str, Any]]:
        """生成分析或预测，输出 SSE 或非流式结果字典。"""

        json_result: dict[str, Any] = {"success": True, "record_id": record.id}
        try:
            yield from self._emit_auxiliary_header(
                record,
                in_chat=in_chat,
                stream=stream,
                json_result=json_result,
            )
            if action_type == "analysis":
                yield from self._stream_analysis(
                    record=record,
                    language=language,
                    assistant_name=assistant_name,
                    workspace_id=workspace_id,
                    model_id=model_id,
                    model_name=model_name,
                    in_chat=in_chat,
                    stream=stream,
                    json_result=json_result,
                )
            elif action_type == "predict":
                yield from self._stream_predict(
                    record=record,
                    language=language,
                    assistant_name=assistant_name,
                    model_id=model_id,
                    model_name=model_name,
                    in_chat=in_chat,
                    stream=stream,
                    json_result=json_result,
                )
            else:
                raise SingleMessageError(f"Type {action_type} Not Found")

            self._finish(record.id or 0)
            if not stream:
                yield json_result
        except Exception as exc:
            traceback.print_exc()
            if isinstance(exc, SingleMessageError):
                error_msg = str(exc)
            else:
                error_msg = orjson.dumps(
                    {
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=1),
                    }
                ).decode()
            if record.id is not None:
                self._save_error(record.id, error_msg)
            if in_chat:
                yield encode_sse_event("error", content=error_msg)
            elif stream:
                yield "&#x274c; **ERROR:**\n"
                yield f"> {error_msg}\n"
            else:
                json_result["success"] = False
                json_result["message"] = error_msg
                yield json_result

    def _build_recommended_question_data(
        self,
        *,
        record: AuxiliaryRecordView,
        current_user: Any,
        current_assistant: Any | None,
        articles_number: int,
        language: str,
        assistant_name: str,
    ) -> RecommendedQuestionGenerationData:
        question = record.question or ""
        schema = self._load_recommend_schema(
            current_user=current_user,
            current_assistant=current_assistant,
            datasource_id=record.datasource,
            question=question,
        )
        return RecommendedQuestionGenerationData(
            record_id=record.id or 0,
            question=question,
            schema=schema,
            datasource_id=record.datasource,
            language=language,
            assistant_name=assistant_name,
            articles_number=articles_number,
        )

    def _load_recommend_schema(
        self,
        *,
        current_user: Any,
        current_assistant: Any | None,
        datasource_id: int | None,
        question: str,
    ) -> str:
        if datasource_id is None:
            return ""
        if is_dynamic_assistant(current_assistant):
            return load_assistant_schema(
                current_assistant,
                datasource_id=int(datasource_id),
                question=question,
                embedding=False,
            )
        context = self._schema_context_service.build(
            current_user,
            int(datasource_id),
            question,
            embedding=False,
            include_sample_data=False,
        )
        return context.schema

    def _stream_analysis(
        self,
        *,
        record: AuxiliaryRecordView,
        language: str,
        assistant_name: str,
        workspace_id: int,
        model_id: int | None,
        model_name: str | None,
        in_chat: bool,
        stream: bool,
        json_result: dict[str, Any],
    ) -> Iterator[str]:
        generation_data = self._build_analysis_data(
            record=record,
            language=language,
            assistant_name=assistant_name,
            workspace_id=workspace_id,
        )
        messages = self._analysis_prediction_service.prepare(generation_data)
        log = self._chat_log_service.start_log(
            ai_modal_id=model_id,
            ai_modal_name=model_name,
            operate=OperationEnum.ANALYSIS,
            record_id=record.id,
            full_message=build_role_prompt_log(messages),
        )
        full_text = ""
        for event in self._analysis_prediction_service.generate(
            generation_data,
            messages,
        ):
            if event.kind == "chunk":
                full_text += event.content
                if in_chat:
                    yield encode_sse_event(
                        "analysis-result",
                        content=event.content,
                        reasoning_content=event.reasoning_content,
                    )
                elif stream:
                    yield event.content
                continue
            self._chat_log_service.end_log(
                log,
                build_role_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )
        if in_chat:
            yield encode_sse_event("info", msg="analysis generated")
            yield encode_sse_event("analysis_finish")
        elif stream:
            yield "\n\n"
        if not stream:
            json_result["content"] = full_text

    def _stream_predict(
        self,
        *,
        record: AuxiliaryRecordView,
        language: str,
        assistant_name: str,
        model_id: int | None,
        model_name: str | None,
        in_chat: bool,
        stream: bool,
        json_result: dict[str, Any],
    ) -> Iterator[str]:
        generation_data = self._build_predict_data(
            record=record,
            language=language,
            assistant_name=assistant_name,
        )
        messages = self._analysis_prediction_service.prepare(generation_data)
        log = self._chat_log_service.start_log(
            ai_modal_id=model_id,
            ai_modal_name=model_name,
            operate=OperationEnum.PREDICT_DATA,
            record_id=record.id,
            full_message=build_role_prompt_log(messages),
        )
        full_text = ""
        for event in self._analysis_prediction_service.generate(
            generation_data,
            messages,
        ):
            if event.kind == "chunk":
                full_text += event.content
                if in_chat:
                    yield encode_sse_event(
                        "predict-result",
                        content=event.content,
                        reasoning_content=event.reasoning_content,
                    )
                continue
            self._chat_log_service.end_log(
                log,
                build_role_prompt_log(messages, event.content),
                reasoning_content=event.reasoning_content,
                token_usage=event.token_usage,
            )

        if in_chat:
            yield encode_sse_event("info", msg="predict generated")

        has_data = self._save_predict_data(record.id or 0, full_text)
        if has_data:
            if in_chat:
                yield encode_sse_event("predict-success")
            elif stream:
                yield full_text + "\n\n"
            else:
                json_result["origin_data"] = self._history_query_service.get_chart_data(
                    record.id or 0
                )
                json_result["predict_data"] = (
                    self._history_query_service.get_predict_data(record.id or 0)
                )
        else:
            if in_chat:
                yield encode_sse_event("predict-failed")
            elif stream:
                yield full_text + "\n\n"
            if not stream:
                json_result["success"] = False
                json_result["message"] = full_text
        if in_chat:
            yield encode_sse_event("predict_finish")

    def _build_analysis_data(
        self,
        *,
        record: AuxiliaryRecordView,
        language: str,
        assistant_name: str,
        workspace_id: int,
    ) -> AnalysisPredictionGenerationData:
        fields, data = self._load_chart_fields_and_data(record.id or 0)
        terminologies, term_items = self._generation_context_service.build_term_context(
            workspace_id,
            record.dataset_id,
            record.question or "",
        )
        term_log = self._chat_log_service.start_log(
            operate=OperationEnum.FILTER_TERMS,
            record_id=record.id,
            local_operation=True,
        )
        self._chat_log_service.end_log(term_log, term_items)
        return AnalysisPredictionGenerationData(
            record_id=record.id or 0,
            generation_type=ChatRecordAuxiliaryType.ANALYSIS,
            fields=fields,
            data=data,
            language=language,
            assistant_name=assistant_name,
            terminologies=terminologies,
        )

    def _build_predict_data(
        self,
        *,
        record: AuxiliaryRecordView,
        language: str,
        assistant_name: str,
    ) -> AnalysisPredictionGenerationData:
        fields, data = self._load_chart_fields_and_data(record.id or 0)
        return AnalysisPredictionGenerationData(
            record_id=record.id or 0,
            generation_type=ChatRecordAuxiliaryType.PREDICT,
            fields=fields,
            data=data,
            language=language,
            assistant_name=assistant_name,
        )

    def _load_chart_fields_and_data(self, record_id: int) -> tuple[str, str]:
        chart_info = self._history_query_service.get_chart_config(record_id)
        fields = orjson.dumps(format_chart_fields(chart_info)).decode()
        chart_data = self._history_query_service.get_chart_data(record_id)
        data = orjson.dumps(chart_data.get("data")).decode()
        return fields, data

    def _save_predict_data(self, record_id: int, content: str) -> bool:
        json_str = extract_nested_json(content) or ""
        self._chat_record_service.project_auxiliary_by_id(
            record_id,
            ChatRecordAuxiliaryProjection(predict_data=json_str),
        )
        return bool(json_str)

    def _finish(self, record_id: int) -> None:
        self._chat_record_service.transition_by_id(
            record_id,
            ChatRecordStatus.SUCCEEDED,
        )

    def _save_error(self, record_id: int, message: str) -> None:
        record = self._chat_record_service.transition_by_id(
            record_id,
            ChatRecordStatus.FAILED,
            error=message,
        )
        if record.finish_time is not None:
            self._chat_log_service.finalize_pending_logs(
                record.id or record_id,
                record.finish_time,
            )

    @staticmethod
    def _emit_auxiliary_header(
        record: AuxiliaryRecordView,
        *,
        in_chat: bool,
        stream: bool,
        json_result: dict[str, Any],
    ) -> Iterator[str]:
        if in_chat:
            yield encode_sse_event("id", id=record.id)
            return
        if stream:
            yield f"> record_id: {record.id}\n"
            yield f"> {record.question}\n\n"
        else:
            json_result["record_id"] = record.id


__all__ = ["AuxiliaryGenerationService", "AuxiliaryRecordView"]
