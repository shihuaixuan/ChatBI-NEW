"""ChatBI 对 Workflow API 通用端口的业务实现。"""

from __future__ import annotations

from typing import Any, Protocol

import orjson
from sqlmodel import Session, select

from apps.chatbi.composition import build_chat_record_service
from apps.chatbi.models import ExecutionBindingData
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
from apps.conversation import (
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResultProjection,
)
from apps.conversation.composition import build_conversation_service
from apps.semantic.composition import build_semantic_dataset_catalog_service
from sqlbot_platform.workflow_engine.api.extension import (
    ChatQueryPreparation,
    WorkflowApiRequestError,
    WorkflowRunProjectionError,
)
from sqlbot_platform.workflow_engine.domain.definition import WorkflowDefinition
from sqlbot_platform.workflow_engine.domain.run import RunStatus, WorkflowRun
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowRunModel,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.run_repository import (
    RunOwnershipError,
    RunRepository,
)
from sqlbot_platform.workflow_engine.ports.run_store import RunStore
from sqlbot_platform.workflow_engine.runtime.graph_runtime import GraphRuntime


class GraphResultNotProjectableError(WorkflowRunProjectionError):
    """Graph 成功但无法形成用户可见历史快照。"""


class GraphRecordProjectionGateway(Protocol):
    def project(
        self,
        *,
        record_id: int,
        chat_id: int,
        run_id: str,
        status: str,
        variables: dict[str, Any],
    ) -> Any: ...


class GraphChatRecordProjector:
    """把 Graph Run 同步投影为 ChatBI 的 ChatRecord 快照。"""

    def __init__(
        self,
        session: Session,
        gateway: GraphRecordProjectionGateway,
    ) -> None:
        self._session = session
        self._gateway = gateway

    def project(self, run: WorkflowRun) -> Any | None:
        """更新绑定的聊天记录；独立 Run 不生成历史投影。"""

        record_id = run.context.request.get("record_id")
        chat_id = run.context.request.get("chat_id")
        if record_id is None and chat_id is None:
            return None
        if record_id is None or chat_id is None:
            raise GraphResultNotProjectableError("GRAPH_CHAT_OWNERSHIP_INCOMPLETE")

        try:
            return self._gateway.project(
                record_id=int(record_id),
                chat_id=int(chat_id),
                run_id=run.run_id,
                status=run.status.value,
                variables=run.context.variables,
            )
        except ValueError as exc:
            raise GraphResultNotProjectableError(str(exc)) from exc

    def project_model(self, run: WorkflowRunModel) -> Any | None:
        """复用通用仓储转换规则投影 ORM Run。"""

        try:
            domain_run = RunRepository(self._session).to_domain(run)
        except RunOwnershipError as exc:
            raise GraphResultNotProjectableError(str(exc)) from exc
        return self.project(domain_run)


class ChatProjectingRunStore:
    """在 Run 保存事务中同步维护 ChatRecord 历史投影。"""

    def __init__(self, base: RunStore, projector: GraphChatRecordProjector) -> None:
        self._base = base
        self._projector = projector

    def create(self, run: WorkflowRun) -> WorkflowRun:
        """创建 Run 后同步投影，但不提交外层事务。"""

        created = self._base.create(run)
        self._projector.project(created)
        return created

    def get(self, run_id: str) -> WorkflowRun:
        """读取操作直接委托给基础 RunStore。"""

        return self._base.get(run_id)

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun:
        """保存 Run 后同步投影，但不提交外层事务。"""

        saved = self._base.save(run, expected_version)
        self._projector.project(saved)
        return saved


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
    ) -> Any:
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
            run_id=run_id,
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
        try:
            chat = build_conversation_service(self._session).get_owned_in_workspace(
                user_id=user_id,
                workspace_id=workspace_id,
                chat_id=chat_id,
            )
        except ValueError as exc:
            raise WorkflowApiRequestError(404, "CHAT_NOT_FOUND") from exc
        if chat is None:
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
        chat = build_conversation_service(self._session).get_owned_in_workspace(
            user_id=user_id,
            workspace_id=workspace_id,
            chat_id=chat_id,
        )
        record = build_chat_record_service(self._session).create(
            ChatRecordCreateData(
                chat_id=chat_id,
                user_id=user_id,
                question=question,
                dataset_id=dataset_id,
                datasource_id=chat.datasource,
                engine_type=chat.engine_type,
                execution_type=ChatRecordExecutionType.GRAPH,
                run_id=run_id,
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

    def build_run_store(self, base: RunStore) -> RunStore:
        """为关联会话的 Graph Run 装配 ChatRecord 同步投影。"""

        return ChatProjectingRunStore(base, self._build_record_projector())

    def project_run_model(self, run: WorkflowRunModel) -> Any | None:
        """投影控制操作直接更新的持久化 Run。"""

        return self._build_record_projector().project_model(run)

    def _build_record_projector(self) -> GraphChatRecordProjector:
        return GraphChatRecordProjector(
            self._session,
            ChatRecordProjectionGateway(self._session),
        )

    def _build_conversation_context(
        self,
        *,
        workspace_id: int,
        user_id: int,
        question: str,
        dataset_id: int,
        chat_record: Any,
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
        chat_record: Any,
    ) -> dict[str, Any]:
        records = build_chat_record_service(
            self._session
        ).list_recent_successful_graph(
            chat_id=chat_record.chat_id,
            exclude_record_id=chat_record.id,
            user_id=user_id,
            dataset_id=dataset_id,
        )
        for record in records:
            run = self._session.exec(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.run_id == record.run_id,
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
        record: Any,
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
    "ChatProjectingRunStore",
    "ChatRecordProjectionGateway",
    "GraphChatRecordProjector",
    "GraphResultNotProjectableError",
    "build_chatbi_workflow_api_extension",
]
