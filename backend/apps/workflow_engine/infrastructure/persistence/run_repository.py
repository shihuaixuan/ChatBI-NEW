from datetime import datetime, timezone

from sqlmodel import Session, select

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import WorkflowRunModel


class RunVersionConflictError(RuntimeError):
    pass


class RunOwnershipError(RuntimeError):
    """Run 物理归属与领域 Context 无法形成一致快照。"""


class RunOwnershipIncompleteError(RunOwnershipError):
    """Run 的物理归属字段未成对提供。"""


class RunOwnershipConflictError(RunOwnershipError):
    """Run 的物理归属与 Context 已提供的归属冲突。"""


def _merge_run_ownership(
    context: WorkflowContext,
    chat_id: int | None,
    record_id: int | None,
) -> WorkflowContext:
    """以物理列为真源校验并补齐领域 Context 的 Run 归属。"""
    merged = context.model_copy(deep=True)
    request = merged.request
    if (chat_id is None) != (record_id is None):
        raise RunOwnershipIncompleteError("GRAPH_CHAT_OWNERSHIP_INCOMPLETE")
    if chat_id is not None and (
        type(chat_id) is not int or type(record_id) is not int
    ):
        raise RunOwnershipConflictError("GRAPH_CHAT_OWNERSHIP_CONFLICT")
    # Context 一旦显式提供归属，必须同时满足精确整数类型和值一致。
    if (
        "chat_id" in request
        and (
            type(request["chat_id"]) is not int or request["chat_id"] != chat_id
        )
        or "record_id" in request
        and (
            type(request["record_id"]) is not int
            or request["record_id"] != record_id
        )
    ):
        raise RunOwnershipConflictError("GRAPH_CHAT_OWNERSHIP_CONFLICT")

    if chat_id is None and record_id is None:
        request.pop("chat_id", None)
        request.pop("record_id", None)
        return merged

    request["chat_id"] = chat_id
    request["record_id"] = record_id
    return merged


class RunRepository:
    """WorkflowRun 的数据库仓储。

    仓储边界只暴露领域对象，不把 SQLModel 实例泄露到 Runtime 层。这样后续更换
    ORM 或增加缓存时，不会影响图运行时的领域协议。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, run: WorkflowRun, oid: int | None = None, user_id: int | None = None, request_id: str | None = None) -> WorkflowRun:
        context_request_id = run.context.request.get("request_id")
        chat_id = run.context.request.get("chat_id")
        record_id = run.context.request.get("record_id")
        context = _merge_run_ownership(run.context, chat_id, record_id)
        model = WorkflowRunModel(
            run_id=run.run_id,
            oid=oid or int(run.context.request.get("tenant_id", 1)),
            user_id=user_id or int(run.context.request.get("user_id", 0)),
            # 物理列是交互式 Run 归属的持久化真源，数据库约束保证两列同空或同非空。
            chat_id=context.request.get("chat_id"),
            record_id=context.request.get("record_id"),
            request_id=request_id or (str(context_request_id) if context_request_id is not None else None),
            definition_name=run.definition_name,
            definition_version=run.definition_version,
            definition_digest=run.definition_digest,
            status=run.status.value,
            current_node=run.current_node,
            context=context.model_dump(mode="json"),
            request=context.request,
            output={},
            version=run.version,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        self._session.add(model)
        self._session.flush()
        return self.to_domain(model)

    def get(self, run_id: str) -> WorkflowRun:
        model = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one()
        return self.to_domain(model)

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun:
        model = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run.run_id)).one()
        if model.version != expected_version:
            raise RunVersionConflictError(
                f"RUN_VERSION_CONFLICT: expected={expected_version}, actual={model.version}"
            )
        # 先拒绝数据库既有分裂状态和调用方冲突，确保失败发生在任何 ORM 写入之前。
        _merge_run_ownership(
            WorkflowContext.model_validate(model.context),
            model.chat_id,
            model.record_id,
        )
        context = _merge_run_ownership(run.context, model.chat_id, model.record_id)
        model.status = run.status.value
        model.current_node = run.current_node
        model.context = context.model_dump(mode="json")
        model.error_code = None if run.status is not RunStatus.FAILED else model.error_code
        model.version = expected_version + 1
        model.updated_at = run.updated_at or datetime.now(timezone.utc)
        self._session.add(model)
        self._session.flush()
        return self.to_domain(model)

    def to_domain(self, model: WorkflowRunModel) -> WorkflowRun:
        """使用仓储的唯一规则把 ORM 快照转换为领域 Run。"""
        context = _merge_run_ownership(
            WorkflowContext.model_validate(model.context),
            model.chat_id,
            model.record_id,
        )
        return WorkflowRun(
            run_id=model.run_id,
            definition_name=model.definition_name,
            definition_version=model.definition_version,
            definition_digest=model.definition_digest,
            status=RunStatus(model.status),
            current_node=model.current_node,
            context=context,
            version=model.version,
            created_at=model.created_at or datetime.now(timezone.utc),
            updated_at=model.updated_at or datetime.now(timezone.utc),
        )
