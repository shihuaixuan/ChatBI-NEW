"""基于 SQLModel/SQLAlchemy 的会话历史读取与执行日志读写仓储。"""

from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import and_, func, update
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from apps.conversation.models import (
    Chat,
    ChatLog,
    ChatLogHandle,
    ChatRecord,
    ChatRecordLiveQuery,
    ChatRecordResult,
    OperationEnum,
    TypeEnum,
)


def _handle_from_log(log: ChatLog) -> ChatLogHandle:
    """把 ChatLog ORM 投影为可传递句柄。"""

    return ChatLogHandle(
        id=log.id,
        pid=log.pid,
        type=log.type.value if isinstance(log.type, TypeEnum) else log.type,
        operate=(
            log.operate.value
            if isinstance(log.operate, OperationEnum)
            else log.operate
        ),
        ai_modal_id=log.ai_modal_id,
        base_modal=log.base_modal,
        messages=log.messages,
        reasoning_content=log.reasoning_content,
        token_usage=log.token_usage,
        start_time=log.start_time,
        finish_time=log.finish_time,
        local_operation=log.local_operation,
        error=log.error,
    )


class SQLModelChatHistoryRepository:
    """会话历史读取与日志读写的 SQLModel 仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_record_projection(
        self,
        *,
        chat_id: int,
        user_id: int,
        with_data: bool,
    ) -> list[ChatRecordResult]:
        sql_alias_log = aliased(ChatLog)
        chart_alias_log = aliased(ChatLog)
        analysis_alias_log = aliased(ChatLog)
        predict_alias_log = aliased(ChatLog)

        base_columns: list[Any] = [
            ChatRecord.id,
            ChatRecord.chat_id,
            ChatRecord.create_time,
            ChatRecord.finish_time,
            ChatRecord.question,
            ChatRecord.sql_answer,
            ChatRecord.sql,
            ChatRecord.dataset_id,
            ChatRecord.datasource,
            ChatRecord.chart_answer,
            ChatRecord.chart,
            ChatRecord.analysis,
            ChatRecord.predict,
            ChatRecord.datasource_select_answer,
            ChatRecord.analysis_record_id,
            ChatRecord.predict_record_id,
            ChatRecord.regenerate_record_id,
            ChatRecord.recommended_question,
            ChatRecord.first_chat,
            ChatRecord.finish,
            ChatRecord.error,
            ChatRecord.execution_type,
            ChatRecord.status,
            ChatRecord.trace_id,
        ]

        if with_data:
            data_columns: list[Any] = [
                *base_columns,
                ChatRecord.data,
                ChatRecord.predict_data,
            ]
            stmt = (
                select(*data_columns)
                .where(
                    ChatRecord.create_by == user_id,
                    ChatRecord.chat_id == chat_id,
                )
                .order_by(col(ChatRecord.create_time))
            )
        else:
            projection_columns: list[Any] = [
                *base_columns,
                col(sql_alias_log.reasoning_content).label(
                    "sql_reasoning_content"
                ),
                col(chart_alias_log.reasoning_content).label(
                    "chart_reasoning_content"
                ),
                col(analysis_alias_log.reasoning_content).label(
                    "analysis_reasoning_content"
                ),
                col(predict_alias_log.reasoning_content).label(
                    "predict_reasoning_content"
                ),
            ]
            stmt = (
                select(*projection_columns)
                .outerjoin(
                    sql_alias_log,
                    and_(
                        col(sql_alias_log.pid) == ChatRecord.id,
                        col(sql_alias_log.type) == TypeEnum.CHAT,
                        col(sql_alias_log.operate) == OperationEnum.GENERATE_SQL,
                    ),
                )
                .outerjoin(
                    chart_alias_log,
                    and_(
                        col(chart_alias_log.pid) == ChatRecord.id,
                        col(chart_alias_log.type) == TypeEnum.CHAT,
                        col(chart_alias_log.operate)
                        == OperationEnum.GENERATE_CHART,
                    ),
                )
                .outerjoin(
                    analysis_alias_log,
                    and_(
                        col(analysis_alias_log.pid) == ChatRecord.id,
                        col(analysis_alias_log.type) == TypeEnum.CHAT,
                        col(analysis_alias_log.operate) == OperationEnum.ANALYSIS,
                    ),
                )
                .outerjoin(
                    predict_alias_log,
                    and_(
                        col(predict_alias_log.pid) == ChatRecord.id,
                        col(predict_alias_log.type) == TypeEnum.CHAT,
                        col(predict_alias_log.operate)
                        == OperationEnum.PREDICT_DATA,
                    ),
                )
                .where(
                    ChatRecord.create_by == user_id,
                    ChatRecord.chat_id == chat_id,
                )
                .order_by(col(ChatRecord.create_time))
            )

        result = self._session.execute(stmt).all()

        record_ids = [row.id for row in result]
        token_usage_map = self._aggregate_token_usage(record_ids)

        record_list: list[ChatRecordResult] = []
        for row in result:
            duration = None
            if row.create_time and row.finish_time:
                try:
                    duration = (row.finish_time - row.create_time).total_seconds()
                except Exception:
                    duration = None

            total_tokens = token_usage_map.get(row.id, 0)

            if not with_data:
                record_list.append(
                    ChatRecordResult(
                        id=row.id,
                        chat_id=row.chat_id,
                        create_time=row.create_time,
                        finish_time=row.finish_time,
                        duration=duration,
                        total_tokens=total_tokens,
                        question=row.question,
                        sql_answer=row.sql_answer,
                        sql=row.sql,
                        dataset_id=row.dataset_id,
                        datasource=row.datasource,
                        chart_answer=row.chart_answer,
                        chart=row.chart,
                        analysis=row.analysis,
                        predict=row.predict,
                        datasource_select_answer=row.datasource_select_answer,
                        analysis_record_id=row.analysis_record_id,
                        predict_record_id=row.predict_record_id,
                        regenerate_record_id=row.regenerate_record_id,
                        recommended_question=row.recommended_question,
                        first_chat=row.first_chat,
                        finish=row.finish,
                        error=row.error,
                        execution_type=row.execution_type,
                        status=row.status,
                        trace_id=row.trace_id,
                        sql_reasoning_content=row.sql_reasoning_content,
                        chart_reasoning_content=row.chart_reasoning_content,
                        analysis_reasoning_content=row.analysis_reasoning_content,
                        predict_reasoning_content=row.predict_reasoning_content,
                    )
                )
            else:
                record_list.append(
                    ChatRecordResult(
                        id=row.id,
                        chat_id=row.chat_id,
                        create_time=row.create_time,
                        finish_time=row.finish_time,
                        duration=duration,
                        total_tokens=total_tokens,
                        question=row.question,
                        sql_answer=row.sql_answer,
                        sql=row.sql,
                        dataset_id=row.dataset_id,
                        datasource=row.datasource,
                        chart_answer=row.chart_answer,
                        chart=row.chart,
                        analysis=row.analysis,
                        predict=row.predict,
                        datasource_select_answer=row.datasource_select_answer,
                        analysis_record_id=row.analysis_record_id,
                        predict_record_id=row.predict_record_id,
                        regenerate_record_id=row.regenerate_record_id,
                        recommended_question=row.recommended_question,
                        first_chat=row.first_chat,
                        finish=row.finish,
                        error=row.error,
                        execution_type=row.execution_type,
                        status=row.status,
                        trace_id=row.trace_id,
                        data=row.data,
                        predict_data=row.predict_data,
                    )
                )

        return record_list

    def _aggregate_token_usage(self, record_ids: list[int]) -> dict[int, int]:
        token_usage_map: dict[int, int] = {}
        if not record_ids:
            return token_usage_map

        log_stmt = select(ChatLog.pid, ChatLog.token_usage).where(
            col(ChatLog.pid).in_(record_ids),
            col(ChatLog.local_operation).is_(False),
            ChatLog.operate != OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
            col(ChatLog.token_usage).is_not(None),
        )
        for pid, token_usage in self._session.execute(log_stmt).all():
            if not pid or token_usage is None:
                continue
            tokens_to_add = 0
            if isinstance(token_usage, dict):
                if token_usage and "total_tokens" in token_usage:
                    token_value = token_usage["total_tokens"]
                    if isinstance(token_value, (int, float)):
                        tokens_to_add = int(token_value)
            elif isinstance(token_usage, (int, float)):
                tokens_to_add = int(token_usage)
            if tokens_to_add > 0:
                token_usage_map[pid] = token_usage_map.get(pid, 0) + tokens_to_add
        return token_usage_map

    def get_chart_config(self, chat_record_id: int) -> dict[str, Any]:
        stmt = select(ChatRecord.chart).where(ChatRecord.id == chat_record_id)
        for row in self._session.execute(stmt):
            try:
                data = orjson.loads(row.chart)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def get_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        conditions: list[Any] = [ChatRecord.id == chat_record_id]
        if user_id is not None:
            conditions.append(ChatRecord.create_by == user_id)
        stmt = select(ChatRecord.data).where(*conditions)
        for row in self._session.execute(stmt):
            try:
                data = orjson.loads(row.data)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def get_predict_data(
        self,
        chat_record_id: int,
        *,
        user_id: int | None = None,
    ) -> Any:
        conditions: list[Any] = [ChatRecord.id == chat_record_id]
        if user_id is not None:
            conditions.append(ChatRecord.create_by == user_id)
        stmt = select(ChatRecord.predict_data).where(*conditions)
        for row in self._session.execute(stmt):
            try:
                return orjson.loads(row.predict_data)
            except Exception:
                pass
        return {}

    def get_live_query(
        self,
        chat_record_id: int,
        *,
        user_id: int,
    ) -> ChatRecordLiveQuery | None:
        stmt = select(ChatRecord.datasource, ChatRecord.sql).where(
            ChatRecord.id == chat_record_id,
            ChatRecord.create_by == user_id,
        )
        row = self._session.execute(stmt).first()
        if row is None:
            return None
        return ChatRecordLiveQuery(datasource_id=row.datasource, sql=row.sql)

    def get_last_execute_sql_error(self, chat_id: int) -> str | None:
        stmt = (
            select(ChatRecord.error)
            .where(ChatRecord.chat_id == chat_id)
            .order_by(col(ChatRecord.create_time).desc())
            .limit(1)
        )
        res = self._session.execute(stmt).scalar()
        if res:
            try:
                obj = orjson.loads(res)
                if isinstance(obj, dict) and obj.get("type") == "exec-sql-err":
                    traceback = obj.get("traceback")
                    if isinstance(traceback, str):
                        return traceback
            except Exception:
                pass
        return None

    def list_recent_questions_for_dataset(
        self,
        *,
        dataset_id: int,
        user_id: int,
        limit: int,
    ) -> list[str]:
        stmt = (
            select(ChatRecord.question)
            .join(Chat, col(ChatRecord.chat_id) == Chat.id)
            .where(
                Chat.dataset_id == dataset_id,
                col(ChatRecord.question).is_not(None),
                ChatRecord.create_by == user_id,
            )
            .group_by(col(ChatRecord.question))
            .order_by(func.max(col(ChatRecord.create_time)).desc())
            .limit(limit)
        )
        return [
            row[0]
            for row in self._session.execute(stmt).all()
            if isinstance(row[0], str) and row[0]
        ]

    def list_logs(self, chat_record_id: int) -> list[ChatLog]:
        stmt = (
            select(ChatLog)
            .where(
                ChatLog.pid == chat_record_id,
                ChatLog.operate
                != OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
            )
            .order_by(col(ChatLog.start_time))
        )
        return [row[0] for row in self._session.execute(stmt).all()]

    def list_logs_by_operation(
        self,
        *,
        chat_id: int,
        operation: OperationEnum,
    ) -> list[ChatLogHandle]:
        stmt = (
            select(ChatLog)
            .where(
                col(ChatLog.pid).in_(
                    select(ChatRecord.id).where(ChatRecord.chat_id == chat_id)
                ),
                ChatLog.type == TypeEnum.CHAT,
                ChatLog.operate == operation,
            )
            .order_by(col(ChatLog.start_time))
        )
        return [
            _handle_from_log(row[0]) for row in self._session.execute(stmt).all()
        ]

    def create_log(
        self,
        *,
        type_: TypeEnum,
        operate: OperationEnum | None,
        pid: int | None,
        ai_modal_id: int | None,
        base_modal: str | None,
        messages: Any,
        start_time: datetime,
        local_operation: bool,
    ) -> ChatLogHandle:
        log = ChatLog(
            type=type_,
            operate=operate,
            pid=pid,
            ai_modal_id=ai_modal_id,
            base_modal=base_modal,
            messages=messages,
            start_time=start_time,
            local_operation=local_operation,
        )
        self._session.add(log)
        self._session.flush()
        self._session.refresh(log)
        handle = _handle_from_log(log)
        self._session.commit()
        return handle

    def finalize_log(
        self,
        log_id: int,
        *,
        messages: Any,
        token_usage: Any,
        finish_time: datetime,
        reasoning_content: str | None,
    ) -> None:
        stmt = (
            update(ChatLog)
            .where(col(ChatLog.id) == log_id)
            .values(
                messages=messages,
                token_usage=token_usage,
                finish_time=finish_time,
                reasoning_content=reasoning_content,
            )
        )
        self._session.execute(stmt)
        self._session.commit()

    def mark_log_error(self, log_id: int) -> None:
        stmt = update(ChatLog).where(col(ChatLog.id) == log_id).values(error=True)
        self._session.execute(stmt)
        self._session.commit()

    def finalize_pending_logs(
        self,
        record_id: int,
        *,
        finish_time: datetime,
    ) -> None:
        stmt = (
            update(ChatLog)
            .where(
                col(ChatLog.pid) == record_id,
                col(ChatLog.finish_time).is_(None),
            )
            .values(finish_time=finish_time, error=True)
        )
        self._session.execute(stmt)
        self._session.commit()


__all__ = ["SQLModelChatHistoryRepository"]
