import asyncio
import json
import threading
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from apps.chatbi.workflow_gateway import (
    Chat,
    ChatRecord,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    build_chat_record_service,
    build_workflow_chat_record_gateway,
)
from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticModel,
)
from apps.workflow.definitions.chatbi_minimal_v1 import (
    build_chatbi_minimal_definition,
)
from apps.workflow.definitions.chatbi_v1 import build_chatbi_v1_definition
from apps.workflow.runtime import (
    build_placeholder_chatbi_runtime,
    build_real_chatbi_v1_runtime,
)
from apps.workflow_engine.api.chat_history import (
    ChatProjectingRunStore,
    GraphChatRecordProjector,
    GraphResultNotProjectableError,
)
from apps.workflow_engine.api.schemas import (
    ControlResponse,
    GraphChatQueryRequest,
    GraphEventListResponse,
    GraphEventResponse,
    GraphPendingInteractionResponse,
    GraphQueryRequest,
    GraphRunResponse,
    GraphTraceNodeResponse,
    GraphTraceResponse,
    InteractionResponseRequest,
)
from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.definition import NodeDefinition, WorkflowDefinition
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.domain.run import RunStatus
from apps.workflow_engine.infrastructure.events.outbox import EventOutbox
from apps.workflow_engine.infrastructure.events.stream import EventStream
from apps.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)
from apps.workflow_engine.infrastructure.persistence.run_repository import RunRepository
from apps.workflow_engine.runtime.public_projection import (
    node_display_label,
    node_trace_output_path,
    sanitize_public_output,
)
from common.core.db import engine


def _graph_chat_record_projector(session: Session) -> GraphChatRecordProjector:
    """在业务 API 边界装配 ChatBI 记录投影端口。"""

    return GraphChatRecordProjector(
        session,
        build_workflow_chat_record_gateway(session),
    )


class GraphApiService:
    """Graph API 应用服务。

    当前阶段只负责独立 Run 的创建、查询、事件续传和控制语义。业务图执行会在
    `workflow` 接入后由 Runtime 推进，API 层不依赖旧 Agentic 编排。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_query(
        self,
        current_user: Any,
        request: GraphQueryRequest,
        commit_events: bool = False,
    ) -> GraphRunResponse:
        """创建不关联聊天历史的独立 Graph Run。"""

        run_id = request.run_id or f"graph-{uuid4().hex}"
        dataset_id = self._resolve_dataset_id(current_user.oid, request.dataset_id)
        request_context = {
            "tenant_id": current_user.oid,
            "user_id": current_user.id,
            "question": request.question,
            "dataset_id": dataset_id,
            "source_dataset_id": request.dataset_id,
            "request_id": request.request_id,
        }
        if request.definition_version == "minimal-v1":
            # minimal-v1 仍使用 datasource_id 字段；v1 主链路已切到 dataset_id。
            request_context["datasource_id"] = request.dataset_id
        runtime = self._build_runtime(request.definition_version, commit_events=commit_events)
        created = runtime.create_run(
            run_id=run_id,
            definition_name="chatbi",
            definition_version=request.definition_version,
            context=WorkflowContext(
                request=request_context,
                conversation={"question": request.question},
            ),
        )
        runtime.execute(created.run_id)
        self._session.commit()
        return self._to_run_response(self._load_owned_run(current_user, created.run_id))

    def create_chat_query(
        self,
        current_user: Any,
        chat_id: int,
        request: GraphChatQueryRequest,
        commit_events: bool = False,
    ) -> GraphRunResponse:
        """创建绑定聊天记录并同步投影执行状态的交互式 Graph Run。"""

        run_id = request.run_id or f"graph-{uuid4().hex}"
        chat, dataset_id = self._resolve_chat_query_context(current_user, chat_id, request)

        record = build_chat_record_service(self._session).create(
            ChatRecordCreateData(
                chat_id=chat_id,
                user_id=current_user.id,
                question=request.question,
                dataset_id=dataset_id,
                datasource_id=chat.datasource,
                engine_type=chat.engine_type,
                execution_type=ChatRecordExecutionType.GRAPH,
                trace_id=run_id,
            )
        )
        if record.id is None:
            raise RuntimeError("GRAPH_CHAT_RECORD_ID_MISSING")

        request_context = {
            "tenant_id": current_user.oid,
            "user_id": current_user.id,
            "question": request.question,
            "dataset_id": dataset_id,
            "source_dataset_id": request.dataset_id,
            "request_id": request.request_id,
            "chat_id": chat_id,
            "record_id": record.id,
        }
        run_store = ChatProjectingRunStore(
            RunRepository(self._session),
            _graph_chat_record_projector(self._session),
        )
        runtime = build_real_chatbi_v1_runtime(
            self._session,
            commit_events=commit_events,
            run_store=run_store,
        )
        try:
            created = runtime.create_run(
                run_id=run_id,
                definition_name="chatbi",
                definition_version=request.definition_version,
                context=WorkflowContext(
                    request=request_context,
                    conversation=self._build_conversation_context(
                        current_user=current_user,
                        question=request.question,
                        dataset_id=dataset_id,
                        chat_record=record,
                    ),
                ),
            )
            runtime.execute(created.run_id)
            self._session.commit()
        except GraphResultNotProjectableError as exc:
            self._raise_projection_http_error(exc)
        return self._to_run_response(self._load_owned_run(current_user, created.run_id))

    def _resolve_chat_query_context(
        self,
        current_user: Any,
        chat_id: int,
        request: GraphChatQueryRequest,
    ) -> tuple[Chat, int]:
        """集中校验交互式 Graph 请求的会话归属和数据集边界。"""

        dataset_id = self._resolve_dataset_id(current_user.oid, request.dataset_id)
        chat = self._session.get(Chat, chat_id)
        if chat is None or chat.oid != current_user.oid or chat.create_by != current_user.id:
            raise HTTPException(status_code=404, detail="CHAT_NOT_FOUND")
        if chat.dataset_id is None or int(chat.dataset_id) != dataset_id:
            raise HTTPException(status_code=400, detail="CHAT_DATASET_MISMATCH")
        return chat, dataset_id

    def _build_conversation_context(
        self,
        *,
        current_user: Any,
        question: str,
        dataset_id: int,
        chat_record: ChatRecord | None,
    ) -> dict[str, Any]:
        conversation: dict[str, Any] = {"question": question}
        if chat_record is None or chat_record.id is None:
            return conversation

        previous_context = self._load_previous_semantic_context(
            current_user=current_user,
            dataset_id=dataset_id,
            chat_record=chat_record,
        )
        conversation.update(previous_context)
        return conversation

    def _load_previous_semantic_context(
        self,
        *,
        current_user: Any,
        dataset_id: int,
        chat_record: ChatRecord,
    ) -> dict[str, Any]:
        records = self._session.exec(
            select(ChatRecord)
            .where(
                ChatRecord.chat_id == chat_record.chat_id,
                ChatRecord.id != chat_record.id,
                ChatRecord.create_by == current_user.id,
                ChatRecord.dataset_id == dataset_id,
                ChatRecord.execution_type == "graph",
                ChatRecord.status == RunStatus.SUCCEEDED.value,
                col(ChatRecord.finish).is_(True),
                col(ChatRecord.trace_id).is_not(None),
            )
            .order_by(col(ChatRecord.create_time).desc(), col(ChatRecord.id).desc())
            .limit(10)
        ).all()
        for record in records:
            run = self._session.exec(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.run_id == record.trace_id,
                    WorkflowRunModel.oid == current_user.oid,
                    WorkflowRunModel.user_id == current_user.id,
                    WorkflowRunModel.chat_id == chat_record.chat_id,
                    WorkflowRunModel.record_id == record.id,
                    WorkflowRunModel.status == RunStatus.SUCCEEDED.value,
                )
            ).one_or_none()
            if run is None:
                continue
            run_request = run.context.get("request") if isinstance(run.context, dict) else {}
            if isinstance(run_request, dict) and run_request.get("dataset_id") != dataset_id:
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
        variables = context.get("variables") if isinstance(context.get("variables"), dict) else {}
        rewrite = variables.get("rewrite") if isinstance(variables.get("rewrite"), dict) else {}
        intent = variables.get("intent") if isinstance(variables.get("intent"), dict) else {}

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
        if not isinstance(intent, dict):
            return {}
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

    def _resolve_dataset_id(self, oid: int, dataset_or_datasource_id: int) -> int:
        """兼容旧前端传入 datasource_id，优先返回真实 Semantic dataset_id。"""

        dataset = self._session.get(SemanticDataset, dataset_or_datasource_id)
        if dataset is not None and dataset.oid == oid and dataset.status == 1:
            return dataset_or_datasource_id
        statement = (
            select(SemanticDataset.id)
            .join(
                SemanticDatasetModelConfig,
                SemanticDatasetModelConfig.dataset_id == SemanticDataset.id,
            )
            .join(SemanticModel, SemanticModel.id == SemanticDatasetModelConfig.model_id)
            .where(
                SemanticDataset.oid == oid,
                SemanticDataset.status == 1,
                SemanticDatasetModelConfig.status == 1,
                SemanticModel.status == 1,
                SemanticModel.datasource_id == dataset_or_datasource_id,
            )
            .order_by(
                col(SemanticDatasetModelConfig.is_default).desc(),
                col(SemanticDatasetModelConfig.sort_order).asc(),
                col(SemanticDataset.id).asc(),
            )
            .limit(1)
        )
        resolved = self._session.exec(statement).one_or_none()
        return int(resolved) if resolved is not None else dataset_or_datasource_id

    def _build_runtime(self, definition_version: str, commit_events: bool = False):
        """按请求版本选择当前可用的 ChatBI 图运行时。"""

        if definition_version == "minimal-v1":
            return build_placeholder_chatbi_runtime(self._session, commit_events=commit_events)
        if definition_version == "v1":
            return build_real_chatbi_v1_runtime(self._session, commit_events=commit_events)
        raise HTTPException(status_code=400, detail="UNSUPPORTED_GRAPH_DEFINITION_VERSION")

    def _build_runtime_for_run(
        self,
        run: WorkflowRunModel,
        *,
        commit_events: bool = False,
    ):
        """关联聊天的 Run 使用投影型仓储，独立 Run 保持通用 Runtime。"""

        if run.record_id is not None:
            if run.definition_version != "v1":
                raise HTTPException(status_code=400, detail="GRAPH_CHAT_DEFINITION_UNSUPPORTED")
            run_store = ChatProjectingRunStore(
                RunRepository(self._session),
                _graph_chat_record_projector(self._session),
            )
            return build_real_chatbi_v1_runtime(
                self._session,
                commit_events=commit_events,
                run_store=run_store,
            )
        return self._build_runtime(run.definition_version, commit_events=commit_events)

    def _raise_projection_http_error(self, exc: GraphResultNotProjectableError) -> NoReturn:
        """只在应用边界映射明确的历史投影错误。"""

        self._session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    def get_run(self, current_user: Any, run_id: str) -> GraphRunResponse:
        return self._to_run_response(self._load_owned_run(current_user, run_id))

    def list_events(self, current_user: Any, run_id: str, after_sequence: int = 0) -> GraphEventListResponse:
        self._load_owned_run(current_user, run_id)
        events = EventStream(self._session).list(run_id=run_id, after_sequence=after_sequence)
        return GraphEventListResponse(events=[self._to_event_response(event) for event in events])

    async def stream_events(self, current_user: Any, run_id: str, after_sequence: int = 0) -> AsyncIterator[str]:
        """以 SSE 协议持续推送公开事件，前端用 sequence 做断点续传。"""

        self._load_owned_run(current_user, run_id)
        async for frame in self._stream_run_events(current_user, run_id, after_sequence=after_sequence):
            yield frame

    async def stream_query(self, current_user: Any, request: GraphQueryRequest) -> AsyncIterator[str]:
        """创建 Run 并在同一个 SSE 响应中推送执行事件。"""

        run_id = request.run_id or f"graph-{uuid4().hex}"
        stream_request = request.model_copy(update={"run_id": run_id})
        errors: list[Exception] = []

        def execute_query() -> None:
            with Session(engine) as session:
                try:
                    GraphApiService(session).create_query(
                        current_user,
                        stream_request,
                        commit_events=True,
                    )
                except Exception as exc:  # pragma: no cover - 失败路径通过 SSE 错误帧兜底
                    session.rollback()
                    errors.append(exc)

        worker = threading.Thread(target=execute_query, daemon=True)
        worker.start()
        async for frame in self._stream_run_events(
            current_user,
            run_id,
            after_sequence=0,
            worker=worker,
            errors=errors,
        ):
            yield frame

    def stream_chat_query(
        self,
        current_user: Any,
        chat_id: int,
        request: GraphChatQueryRequest,
    ) -> AsyncIterator[str]:
        """创建交互式 Run，并通过现有 SSE 协议推送执行事件。"""

        # 在构造 StreamingResponse 前完成显式业务校验，确保 404/400 不会退化为 HTTP 200 的 SSE 错误帧。
        self._resolve_chat_query_context(current_user, chat_id, request)
        run_id = request.run_id or f"graph-{uuid4().hex}"
        stream_request = request.model_copy(update={"run_id": run_id})

        async def generate_frames() -> AsyncIterator[str]:
            errors: list[Exception] = []

            def execute_query() -> None:
                with Session(engine) as session:
                    try:
                        GraphApiService(session).create_chat_query(
                            current_user,
                            chat_id,
                            stream_request,
                            commit_events=True,
                        )
                    except Exception as exc:  # pragma: no cover - 原异常由 SSE 错误帧传递
                        session.rollback()
                        errors.append(exc)

            worker = threading.Thread(target=execute_query, daemon=True)
            worker.start()
            async for frame in self._stream_run_events(
                current_user,
                run_id,
                after_sequence=0,
                worker=worker,
                errors=errors,
            ):
                yield frame

        return generate_frames()

    async def _stream_run_events(
        self,
        current_user: Any,
        run_id: str,
        after_sequence: int = 0,
        worker: threading.Thread | None = None,
        errors: list[Exception] | None = None,
    ) -> AsyncIterator[str]:
        last_sequence = after_sequence
        while True:
            self._session.expire_all()
            events = EventStream(self._session).list(run_id=run_id, after_sequence=last_sequence)
            for event in events:
                last_sequence = event.sequence
                yield self._to_sse_frame(self._to_event_response(event))

            run = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one_or_none()
            if run is not None and (run.oid != current_user.oid or run.user_id != current_user.id):
                raise HTTPException(status_code=404, detail="GRAPH_RUN_NOT_FOUND")
            if run is not None:
                worker_running = worker is not None and worker.is_alive()
                reached_stream_boundary = run.status in {
                    RunStatus.SUCCEEDED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                } or run.status == RunStatus.WAITING_INPUT.value
                if reached_stream_boundary and worker_running:
                    # Run 状态可能先于最后一条公开事件提交，等待 worker 完成后再关闭流。
                    await asyncio.sleep(0.5)
                    continue
                if reached_stream_boundary:
                    # worker 已结束后排空最终事件，避免遗漏 run.succeeded/run.failed 等终态帧。
                    while True:
                        trailing_events = EventStream(self._session).list(
                            run_id=run_id,
                            after_sequence=last_sequence,
                        )
                        if not trailing_events:
                            break
                        for event in trailing_events:
                            last_sequence = event.sequence
                            yield self._to_sse_frame(self._to_event_response(event))
                    return
            if worker is not None and not worker.is_alive() and run is None:
                if errors:
                    yield self._to_sse_error_frame(errors[-1])
                return

            await asyncio.sleep(0.5)

    def get_trace(self, current_user: Any, run_id: str) -> GraphTraceResponse:
        run = self._load_owned_run(current_user, run_id)
        events = EventStream(self._session).list(run_id=run_id, after_sequence=0)
        context = run.context or {}
        trace_nodes = self._trace_nodes(run.definition_version)
        return GraphTraceResponse(
            run_id=run.run_id,
            status=run.status,
            current_node=run.current_node,
            nodes=[
                GraphTraceNodeResponse(
                    name=node.name,
                    label=node_display_label(node),
                    status=self._trace_node_status(events, node.name),
                    route_reason=self._trace_route_reason(events, node.name),
                    output=self._trace_node_output(context, node, events),
                )
                for node in trace_nodes
            ],
        )

    def answer_interaction(
        self,
        current_user: Any,
        run_id: str,
        interaction_id: str,
        request: InteractionResponseRequest,
        commit_events: bool = False,
    ) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        if run.status != RunStatus.WAITING_INPUT.value:
            raise HTTPException(status_code=409, detail="RUN_NOT_WAITING_INPUT")
        if run.definition_version == "v1" and run.status == RunStatus.WAITING_INPUT.value:
            runtime = self._build_runtime_for_run(run, commit_events=commit_events)
            try:
                resumed = runtime.resume(
                    run_id=run_id,
                    interaction_id=interaction_id,
                    response=request.response,
                    tenant_id=current_user.oid,
                    user_id=current_user.id,
                )
                self._session.commit()
            except GraphResultNotProjectableError as exc:
                self._raise_projection_http_error(exc)
            return ControlResponse(run_id=run_id, status=resumed.status.value)

        interaction = self._session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == run_id,
                InteractionRequestModel.interaction_id == interaction_id,
            )
        ).one_or_none()
        if interaction is None or interaction.status != "pending":
            raise HTTPException(status_code=409, detail="INTERACTION_NOT_PENDING")
        interaction.status = "answered"
        interaction.response = request.response
        interaction.answered_at = datetime.now(timezone.utc)
        self._session.add(interaction)
        self._append_control_event(run_id, "interaction.answered", {"status": "answered"})
        self._session.commit()
        return ControlResponse(run_id=run_id, status="answered")

    async def stream_interaction_response(
        self,
        current_user: Any,
        run_id: str,
        interaction_id: str,
        request: InteractionResponseRequest,
        after_sequence: int = 0,
    ) -> AsyncIterator[str]:
        """提交用户补充信息，并继续以 SSE 推送后续执行事件。"""

        errors: list[Exception] = []

        def answer_and_resume() -> None:
            with Session(engine) as session:
                try:
                    GraphApiService(session).answer_interaction(
                        current_user,
                        run_id,
                        interaction_id,
                        request,
                        commit_events=True,
                    )
                except Exception as exc:  # pragma: no cover - 失败路径通过 SSE 错误帧兜底
                    session.rollback()
                    errors.append(exc)

        worker = threading.Thread(target=answer_and_resume, daemon=True)
        worker.start()
        async for frame in self._stream_run_events(
            current_user,
            run_id,
            after_sequence=after_sequence,
            worker=worker,
            errors=errors,
        ):
            yield frame

    def cancel(self, current_user: Any, run_id: str) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        run.status = RunStatus.CANCELLED.value
        run.version += 1
        run.updated_at = datetime.now(timezone.utc)
        self._session.add(run)
        pending_interactions = self._session.exec(
            select(InteractionRequestModel).where(
                InteractionRequestModel.run_id == run_id,
                InteractionRequestModel.status == "pending",
            )
        ).all()
        for interaction in pending_interactions:
            interaction.status = "cancelled"
            self._session.add(interaction)
        self._append_control_event(run_id, "run.cancelled", {"status": "cancelled"})
        try:
            _graph_chat_record_projector(self._session).project_model(run)
            self._session.commit()
        except GraphResultNotProjectableError as exc:
            self._raise_projection_http_error(exc)
        return ControlResponse(run_id=run_id, status=RunStatus.CANCELLED.value)

    def retry(self, current_user: Any, run_id: str) -> ControlResponse:
        run = self._load_owned_run(current_user, run_id)
        if run.status != RunStatus.FAILED.value:
            raise HTTPException(status_code=409, detail="RUN_NOT_FAILED")
        if run.definition_version == "v1":
            runtime = self._build_runtime_for_run(run)
            try:
                self._restore_v1_run_for_retry(run)
                _graph_chat_record_projector(self._session).project_model(run)
                self._append_control_event(run_id, "run.retry_requested", {"status": "created"})
                self._session.flush()
                retried = runtime.execute(run_id)
                self._session.commit()
            except GraphResultNotProjectableError as exc:
                self._raise_projection_http_error(exc)
            return ControlResponse(run_id=run_id, status=retried.status.value)
        run.status = RunStatus.CREATED.value
        run.current_node = "start"
        run.error_code = None
        run.version += 1
        run.updated_at = datetime.now(timezone.utc)
        self._session.add(run)
        self._append_control_event(run_id, "run.retry_requested", {"status": "created"})
        self._session.commit()
        return ControlResponse(run_id=run_id, status=RunStatus.CREATED.value)

    def _restore_v1_run_for_retry(self, run: WorkflowRunModel) -> None:
        """把失败的 v1 Run 恢复到最近成功节点边界，随后交给 Runtime 继续推进。"""

        checkpoint_context = self._latest_checkpoint_context(run.run_id)
        context = checkpoint_context or dict(run.context or {})
        control = context.setdefault("control", {})
        current_node = str(control.get("current_node") or run.current_node or "classify_question")
        control["current_node"] = current_node
        control["pending_interaction_id"] = None
        if checkpoint_context is None:
            self._rollback_failed_node_visit(control, current_node)
        run.context = context
        run.status = RunStatus.CREATED.value
        run.current_node = current_node
        run.error_code = None
        run.version += 1
        run.updated_at = datetime.now(timezone.utc)
        self._session.add(run)

    def _latest_checkpoint_context(self, run_id: str) -> dict[str, Any] | None:
        checkpoint = self._session.exec(
            select(WorkflowCheckpointModel)
            .where(WorkflowCheckpointModel.run_id == run_id)
            .order_by(col(WorkflowCheckpointModel.sequence).desc())
        ).first()
        return dict(checkpoint.context) if checkpoint is not None else None

    @staticmethod
    def _rollback_failed_node_visit(control: dict[str, Any], current_node: str) -> None:
        """无持久 checkpoint 时，撤销失败节点进入执行前增加的 visit 计数。"""

        loop_iterations = control.get("loop_iterations")
        if not isinstance(loop_iterations, dict):
            control["loop_iterations"] = {}
            return
        try:
            current_count = int(loop_iterations.get(current_node) or 0)
        except (TypeError, ValueError):
            current_count = 0
        if current_count <= 1:
            loop_iterations.pop(current_node, None)
        else:
            loop_iterations[current_node] = current_count - 1

    def _load_owned_run(self, current_user: Any, run_id: str) -> WorkflowRunModel:
        run = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one_or_none()
        if run is None or run.oid != current_user.oid or run.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="GRAPH_RUN_NOT_FOUND")
        return run

    def _append_control_event(self, run_id: str, event_type: str, public_payload: dict[str, Any]) -> None:
        EventOutbox(self._session).append(
            WorkflowEvent(
                event_id=str(uuid4()),
                run_id=run_id,
                sequence=self._next_sequence(run_id),
                event_type=event_type,
                public_payload=public_payload,
                created_at=datetime.now(timezone.utc),
            )
        )

    def _next_sequence(self, run_id: str) -> int:
        latest_sequence = self._session.exec(
            select(func.max(col(WorkflowEventModel.sequence))).where(WorkflowEventModel.run_id == run_id)
        ).one()
        return int(latest_sequence or 0) + 1

    def _to_run_response(self, run: WorkflowRunModel) -> GraphRunResponse:
        context = run.context or {}
        request = run.request or context.get("request", {})
        variables = context.get("variables", {})
        pending_interaction = self._pending_interaction_response(
            run.run_id,
            definition_version=run.definition_version,
        )
        return GraphRunResponse(
            run_id=run.run_id,
            record_id=run.record_id,
            status=run.status,
            current_node=run.current_node,
            output=run.output or {},
            context_summary={
                "question": request.get("question"),
                "dataset_id": request.get("dataset_id"),
                "variables": variables,
                "pending_interaction": pending_interaction.model_dump(mode="json")
                if pending_interaction is not None
                else None,
            },
        )

    def _trace_definition(self, definition_version: str) -> WorkflowDefinition:
        if definition_version == "v1":
            return build_chatbi_v1_definition()
        if definition_version == "minimal-v1":
            return build_chatbi_minimal_definition()
        raise HTTPException(status_code=400, detail="UNSUPPORTED_GRAPH_DEFINITION_VERSION")

    def _trace_nodes(self, definition_version: str) -> list[NodeDefinition]:
        definition = self._trace_definition(definition_version)
        return [
            node
            for node in definition.nodes.values()
            if node_trace_output_path(node) is not None
        ]

    def _trace_node_status(self, events: list[WorkflowEvent], node_name: str) -> str:
        event_types = [event.event_type for event in events if event.node_name == node_name]
        if "node.failed" in event_types:
            return "failed"
        if "node.succeeded" in event_types:
            return "succeeded"
        if "node.started" in event_types:
            return "started"
        return "not_run"

    def _trace_route_reason(self, events: list[WorkflowEvent], node_name: str) -> str | None:
        for event in reversed(events):
            if event.node_name == node_name and event.event_type == "node.routed":
                reason_code = event.public_payload.get("reason_code") if event.public_payload else None
                return str(reason_code) if reason_code is not None else None
        return None

    def _trace_node_output(
        self,
        context: dict[str, Any],
        node: NodeDefinition,
        events: list[WorkflowEvent],
    ) -> Any:
        # 未执行节点不读取共享变量路径，避免把其他节点后写入的结果误归因到该节点。
        if self._trace_node_status(events, node.name) == "not_run":
            return None
        if node.name.startswith("ask_"):
            pending_interaction = self._pending_interaction_response(
                self._run_id_from_events(events),
                node_name=node.name,
                node_label=node_display_label(node),
            )
            if pending_interaction is not None:
                return pending_interaction.model_dump(mode="json")
        output_path = node_trace_output_path(node)
        if output_path is None:
            return None
        output = self._read_context_path(context, output_path)
        return self._sanitize_trace_output(node, output)

    @staticmethod
    def _run_id_from_events(events: list[WorkflowEvent]) -> str | None:
        for event in events:
            return event.run_id
        return None

    def _pending_interaction_response(
        self,
        run_id: str | None,
        node_name: str | None = None,
        definition_version: str | None = None,
        node_label: str | None = None,
    ) -> GraphPendingInteractionResponse | None:
        """读取当前 Run 的待处理交互，用于前端直接渲染用户输入控件。"""

        if not run_id:
            return None
        statement = select(InteractionRequestModel).where(
            InteractionRequestModel.run_id == run_id,
            InteractionRequestModel.status == "pending",
        )
        if node_name is not None:
            statement = statement.where(InteractionRequestModel.node_name == node_name)
        interaction = self._session.exec(statement.order_by(col(InteractionRequestModel.created_at).desc())).first()
        if interaction is None:
            return None
        return GraphPendingInteractionResponse(
            interaction_id=interaction.interaction_id,
            run_id=interaction.run_id,
            node_name=interaction.node_name,
            label=node_label or self._trace_node_label(definition_version, interaction.node_name),
            status=interaction.status,
            prompt=interaction.prompt,
            options=interaction.options or [],
            response_schema=interaction.response_schema or {},
            allowed_update_paths=interaction.allowed_update_paths or [],
        )

    def _trace_node_label(self, definition_version: str | None, node_name: str) -> str | None:
        if not definition_version:
            return None
        node = self._trace_definition(definition_version).nodes.get(node_name)
        return node_display_label(node) if node is not None else None

    def _sanitize_trace_output(self, node: NodeDefinition, output: Any) -> Any:
        """trace 公开输出与事件摘要共用 metadata 驱动的投影策略。"""

        return sanitize_public_output(node, output)

    def _read_context_path(self, context: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = context
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        return current

    def _to_event_response(self, event: WorkflowEvent) -> GraphEventResponse:
        return GraphEventResponse(
            event_id=event.event_id,
            sequence=event.sequence,
            event_type=event.event_type,
            node_name=event.node_name,
            public_payload=event.public_payload,
            created_at=event.created_at.isoformat(),
        )

    @staticmethod
    def _to_sse_frame(event: GraphEventResponse) -> str:
        return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

    @staticmethod
    def _to_sse_error_frame(error: Exception) -> str:
        payload = {
            "event_type": "run.failed",
            "public_payload": {"message": str(error).replace("\n", " ")},
        }
        return f"event: run.failed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
