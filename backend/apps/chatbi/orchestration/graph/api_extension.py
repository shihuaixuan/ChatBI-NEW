"""ChatBI 对 Workflow API 通用端口的业务实现。"""

from __future__ import annotations

from typing import Any

import orjson
from sqlmodel import Session, col, select

from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import (
    Chat,
    ChatRecord,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
    ExecutionBindingData,
)
from apps.chatbi.orchestration.graph.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
)
from apps.chatbi.orchestration.graph.definitions.chatbi_v1 import (
    build_chatbi_v1_definition,
)
from apps.chatbi.orchestration.graph.runtime import (
    build_placeholder_chatbi_runtime,
    build_real_chatbi_v1_runtime,
)
from apps.chatbi.services.planning import (
    ExecutionBindingError,
    resolve_execution_binding,
)
from apps.semantic.composition import build_semantic_dataset_catalog_service
from sqlbot_platform.workflow_engine.api.chat_history import (
    GraphRecordProjectionGateway,
)
from sqlbot_platform.workflow_engine.api.extension import (
    ChatQueryPreparation,
    WorkflowApiRequestError,
)
from sqlbot_platform.workflow_engine.domain.definition import WorkflowDefinition
from sqlbot_platform.workflow_engine.domain.run import RunStatus
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowRunModel,
)
from sqlbot_platform.workflow_engine.ports.run_store import RunStore
from sqlbot_platform.workflow_engine.runtime.graph_runtime import GraphRuntime


class ChatRecordProjectionGateway:
    """把 Workflow Run 终态写入 ChatBI 历史记录。"""

    def __init__(self, session: Session) -> None:
        self._service = build_chat_record_service(session)

    def project(
        self,
        *,
        record_id: int,
        chat_id: int,
        run_id: str,
        status: str,
        variables: dict[str, Any],
    ) -> ChatRecord:
        try:
            record = self._service.get(record_id)
        except ValueError as exc:
            raise ValueError("GRAPH_CHAT_RECORD_NOT_FOUND") from exc
        if record.chat_id != chat_id:
            raise ValueError("GRAPH_CHAT_RECORD_NOT_FOUND")

        result = None
        if status == RunStatus.SUCCEEDED.value:
            final_reply = variables.get("final_reply") or {}
            legacy_answer = variables.get("answer") or {}
            answer = str(
                final_reply.get("final_answer") or legacy_answer.get("answer") or ""
            ).strip()
            if not answer:
                raise ValueError("GRAPH_RESULT_NOT_PROJECTABLE")
            sql_payload = variables.get("sql") or {}
            sql = sql_payload.get("sql") if isinstance(sql_payload, dict) else None
            chart = final_reply.get("chart")
            result = ChatRecordResultProjection(
                answer=answer,
                sql=sql,
                chart=orjson.dumps(chart).decode() if chart is not None else None,
            )

        return self._service.transition(
            record,
            status,
            expected_chat_id=chat_id,
            trace_id=run_id,
            execution_type=ChatRecordExecutionType.GRAPH,
            error="GRAPH_RUN_FAILED" if status == RunStatus.FAILED.value else None,
            result=result,
        )


class ChatBIWorkflowApiExtension:
    """集中提供 ChatBI 图定义、运行时、会话上下文和历史投影。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_dataset_id(
        self,
        workspace_id: int,
        dataset_or_datasource_id: int,
    ) -> int:
        return build_semantic_dataset_catalog_service(
            self._session
        ).resolve_dataset_id(
            workspace_id,
            dataset_or_datasource_id,
        )

    def validate_chat_query(
        self,
        *,
        workspace_id: int,
        user_id: int,
        chat_id: int,
        requested_dataset_id: int,
    ) -> int:
        """集中校验会话归属和数据集执行绑定。"""

        dataset_id = self.resolve_dataset_id(
            workspace_id,
            requested_dataset_id,
        )
        chat = self._session.get(Chat, chat_id)
        if (
            chat is None
            or chat.oid != workspace_id
            or chat.create_by != user_id
        ):
            raise WorkflowApiRequestError(404, "CHAT_NOT_FOUND")
        try:
            binding = resolve_execution_binding(
                ExecutionBindingData(
                    conversation_dataset_id=chat.dataset_id,
                    conversation_datasource_id=chat.datasource,
                    requested_dataset_id=dataset_id,
                    require_dataset=True,
                )
            )
        except ExecutionBindingError as exc:
            raise WorkflowApiRequestError(400, str(exc)) from exc
        if binding.dataset_id is None:
            raise WorkflowApiRequestError(400, "CHAT_DATASET_REQUIRED")
        return binding.dataset_id

    def prepare_chat_query(
        self,
        *,
        workspace_id: int,
        user_id: int,
        chat_id: int,
        question: str,
        requested_dataset_id: int,
        run_id: str,
    ) -> ChatQueryPreparation:
        dataset_id = self.validate_chat_query(
            workspace_id=workspace_id,
            user_id=user_id,
            chat_id=chat_id,
            requested_dataset_id=requested_dataset_id,
        )
        chat = self._session.get(Chat, chat_id)
        if chat is None:
            # validate_chat_query 已校验存在性；这里保护同一事务内的不变量。
            raise RuntimeError("CHAT_DISAPPEARED_DURING_GRAPH_PREPARATION")
        record = build_chat_record_service(self._session).create(
            ChatRecordCreateData(
                chat_id=chat_id,
                user_id=user_id,
                question=question,
                dataset_id=dataset_id,
                datasource_id=chat.datasource,
                engine_type=chat.engine_type,
                execution_type=ChatRecordExecutionType.GRAPH,
                trace_id=run_id,
            )
        )
        if record.id is None:
            raise RuntimeError("GRAPH_CHAT_RECORD_ID_MISSING")
        return ChatQueryPreparation(
            dataset_id=dataset_id,
            record_id=record.id,
            conversation=self._build_conversation_context(
                workspace_id=workspace_id,
                user_id=user_id,
                question=question,
                dataset_id=dataset_id,
                chat_record=record,
            ),
        )

    def build_runtime(
        self,
        definition_version: str,
        *,
        commit_events: bool,
        run_store: RunStore | None = None,
    ) -> GraphRuntime:
        if definition_version == "minimal-v1":
            if run_store is not None:
                raise RuntimeError("MINIMAL_GRAPH_RUN_STORE_UNSUPPORTED")
            return build_placeholder_chatbi_runtime(
                self._session,
                commit_events=commit_events,
            )
        if definition_version == "v1":
            return build_real_chatbi_v1_runtime(
                self._session,
                commit_events=commit_events,
                run_store=run_store,
            )
        raise WorkflowApiRequestError(
            400,
            "UNSUPPORTED_GRAPH_DEFINITION_VERSION",
        )

    def build_definition(self, definition_version: str) -> WorkflowDefinition:
        if definition_version == "v1":
            return build_chatbi_v1_definition()
        if definition_version == "minimal-v1":
            return build_chatbi_minimal_definition()
        raise WorkflowApiRequestError(
            400,
            "UNSUPPORTED_GRAPH_DEFINITION_VERSION",
        )

    def build_record_projection_gateway(
        self,
    ) -> GraphRecordProjectionGateway:
        return ChatRecordProjectionGateway(self._session)

    def _build_conversation_context(
        self,
        *,
        workspace_id: int,
        user_id: int,
        question: str,
        dataset_id: int,
        chat_record: ChatRecord,
    ) -> dict[str, Any]:
        conversation: dict[str, Any] = {"question": question}
        conversation.update(
            self._load_previous_semantic_context(
                workspace_id=workspace_id,
                user_id=user_id,
                dataset_id=dataset_id,
                chat_record=chat_record,
            )
        )
        return conversation

    def _load_previous_semantic_context(
        self,
        *,
        workspace_id: int,
        user_id: int,
        dataset_id: int,
        chat_record: ChatRecord,
    ) -> dict[str, Any]:
        records = self._session.exec(
            select(ChatRecord)
            .where(
                ChatRecord.chat_id == chat_record.chat_id,
                ChatRecord.id != chat_record.id,
                ChatRecord.create_by == user_id,
                ChatRecord.dataset_id == dataset_id,
                ChatRecord.execution_type == ChatRecordExecutionType.GRAPH.value,
                ChatRecord.status == RunStatus.SUCCEEDED.value,
                col(ChatRecord.finish).is_(True),
                col(ChatRecord.trace_id).is_not(None),
            )
            .order_by(
                col(ChatRecord.create_time).desc(),
                col(ChatRecord.id).desc(),
            )
            .limit(10)
        ).all()
        for record in records:
            run = self._session.exec(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.run_id == record.trace_id,
                    WorkflowRunModel.oid == workspace_id,
                    WorkflowRunModel.user_id == user_id,
                    WorkflowRunModel.chat_id == chat_record.chat_id,
                    WorkflowRunModel.record_id == record.id,
                    WorkflowRunModel.status == RunStatus.SUCCEEDED.value,
                )
            ).one_or_none()
            if run is None:
                continue
            run_request = (
                run.context.get("request")
                if isinstance(run.context, dict)
                else {}
            )
            if (
                isinstance(run_request, dict)
                and run_request.get("dataset_id") != dataset_id
            ):
                continue
            projected = self._project_previous_semantic_context(run, record)
            if projected:
                return projected
        return {}

    def _project_previous_semantic_context(
        self,
        run: WorkflowRunModel,
        record: ChatRecord,
    ) -> dict[str, Any]:
        context = run.context if isinstance(run.context, dict) else {}
        variables_value = context.get("variables")
        variables: dict[str, Any] = (
            variables_value if isinstance(variables_value, dict) else {}
        )
        rewrite_value = variables.get("rewrite")
        rewrite: dict[str, Any] = (
            rewrite_value if isinstance(rewrite_value, dict) else {}
        )
        intent_value = variables.get("intent")
        intent: dict[str, Any] = (
            intent_value if isinstance(intent_value, dict) else {}
        )
        projected_intent = self._project_intent_context(intent)
        projected: dict[str, Any] = {
            "last_record_id": record.id,
            "last_run_id": run.run_id,
            "last_question": record.question,
        }
        rewritten_question = rewrite.get("rewritten_question")
        if isinstance(rewritten_question, str) and rewritten_question.strip():
            projected["last_rewritten_question"] = rewritten_question.strip()
        if projected_intent:
            projected["last_intent"] = projected_intent
        return projected

    @staticmethod
    def _project_intent_context(intent: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {
            "intent_type",
            "metric_mentions",
            "time_range",
            "dimension_slots",
            "filter_mentions",
            "query_shape",
        }
        # 只向下一轮暴露语义摘要，避免把内部资产绑定或大对象塞进 prompt。
        return {key: intent[key] for key in allowed_keys if key in intent}


def build_chatbi_workflow_api_extension(
    session: Session,
) -> ChatBIWorkflowApiExtension:
    """按当前数据库会话装配 ChatBI Workflow API 扩展。"""

    return ChatBIWorkflowApiExtension(session)


__all__ = [
    "ChatBIWorkflowApiExtension",
    "ChatRecordProjectionGateway",
    "build_chatbi_workflow_api_extension",
]
