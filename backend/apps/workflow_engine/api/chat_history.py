from datetime import datetime

import orjson
from sqlmodel import Session

from apps.chat.models.chat_model import ChatRecord
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import WorkflowRunModel
from apps.workflow_engine.infrastructure.persistence.run_repository import (
    RunOwnershipError,
    RunRepository,
)
from apps.workflow_engine.ports.run_store import RunStore


class GraphResultNotProjectableError(RuntimeError):
    """Graph 成功但无法形成用户可见历史快照。"""


class GraphChatRecordProjector:
    """把领域 Run 同步投影为同一事务内的 ChatRecord 快照。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project(self, run: WorkflowRun) -> ChatRecord | None:
        """更新绑定的聊天记录；独立 Run 不生成历史投影。"""
        record_id = run.context.request.get("record_id")
        chat_id = run.context.request.get("chat_id")
        if record_id is None and chat_id is None:
            return None
        if record_id is None or chat_id is None:
            raise GraphResultNotProjectableError("GRAPH_CHAT_OWNERSHIP_INCOMPLETE")

        record = self._session.get(ChatRecord, int(record_id))
        if record is None or record.chat_id != int(chat_id):
            raise GraphResultNotProjectableError("GRAPH_CHAT_RECORD_NOT_FOUND")

        success_answer: str | None = None
        success_sql: str | None = None
        success_chart: str | None = None
        if run.status is RunStatus.SUCCEEDED:
            variables = run.context.variables
            final_reply = variables.get("final_reply") or {}
            legacy_answer = variables.get("answer") or {}
            success_answer = str(
                final_reply.get("final_answer") or legacy_answer.get("answer") or ""
            ).strip()
            if not success_answer:
                raise GraphResultNotProjectableError("GRAPH_RESULT_NOT_PROJECTABLE")
            sql_payload = variables.get("sql") or {}
            success_sql = (
                sql_payload.get("sql") if isinstance(sql_payload, dict) else None
            )
            chart = final_reply.get("chart")
            success_chart = orjson.dumps(chart).decode() if chart is not None else None

        record.trace_id = run.run_id
        record.execution_type = "graph"
        record.status = run.status.value
        record.finish = run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        record.finish_time = datetime.now() if record.finish else None

        if run.status is RunStatus.SUCCEEDED:
            record.sql_answer = success_answer
            record.sql = success_sql
            record.chart = success_chart
            record.error = None
        elif run.status is RunStatus.FAILED:
            record.error = "GRAPH_RUN_FAILED"
        elif run.status in {RunStatus.CREATED, RunStatus.RUNNING}:
            # 重试沿用原记录，进入运行态时清理旧终态快照。
            record.finish = False
            record.finish_time = None
            record.error = None

        self._session.add(record)
        self._session.flush()
        return record

    def project_model(self, run: WorkflowRunModel) -> ChatRecord | None:
        """复用仓储转换规则投影 ORM Run。"""
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
