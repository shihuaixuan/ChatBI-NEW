from typing import Any, Protocol

from sqlmodel import Session

from sqlbot_platform.workflow_engine.domain.run import WorkflowRun
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowRunModel,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.run_repository import (
    RunOwnershipError,
    RunRepository,
)
from sqlbot_platform.workflow_engine.ports.run_store import RunStore


class GraphResultNotProjectableError(RuntimeError):
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
    """把领域 Run 同步投影为同一事务内的 ChatRecord 快照。"""

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
