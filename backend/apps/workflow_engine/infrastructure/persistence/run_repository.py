from datetime import datetime, timezone

from sqlmodel import Session, select

from apps.workflow_engine.domain.context import WorkflowContext
from apps.workflow_engine.domain.run import RunStatus, WorkflowRun
from apps.workflow_engine.infrastructure.persistence.models import WorkflowRunModel


class RunVersionConflictError(RuntimeError):
    pass


class RunRepository:
    """WorkflowRun 的数据库仓储。

    仓储边界只暴露领域对象，不把 SQLModel 实例泄露到 Runtime 层。这样后续更换
    ORM 或增加缓存时，不会影响图运行时的领域协议。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, run: WorkflowRun, oid: int | None = None, user_id: int | None = None, request_id: str | None = None) -> WorkflowRun:
        context_request_id = run.context.request.get("request_id")
        model = WorkflowRunModel(
            run_id=run.run_id,
            oid=oid or int(run.context.request.get("tenant_id", 1)),
            user_id=user_id or int(run.context.request.get("user_id", 0)),
            request_id=request_id or (str(context_request_id) if context_request_id is not None else None),
            definition_name=run.definition_name,
            definition_version=run.definition_version,
            definition_digest=run.definition_digest,
            status=run.status.value,
            current_node=run.current_node,
            context=run.context.model_dump(mode="json"),
            request=run.context.request,
            output={},
            version=run.version,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_domain(model)

    def get(self, run_id: str) -> WorkflowRun:
        model = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)).one()
        return self._to_domain(model)

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun:
        model = self._session.exec(select(WorkflowRunModel).where(WorkflowRunModel.run_id == run.run_id)).one()
        if model.version != expected_version:
            raise RunVersionConflictError(
                f"RUN_VERSION_CONFLICT: expected={expected_version}, actual={model.version}"
            )
        model.status = run.status.value
        model.current_node = run.current_node
        model.context = run.context.model_dump(mode="json")
        model.error_code = None if run.status is not RunStatus.FAILED else model.error_code
        model.version = expected_version + 1
        model.updated_at = run.updated_at or datetime.now(timezone.utc)
        self._session.add(model)
        self._session.flush()
        return self._to_domain(model)

    def _to_domain(self, model: WorkflowRunModel) -> WorkflowRun:
        return WorkflowRun(
            run_id=model.run_id,
            definition_name=model.definition_name,
            definition_version=model.definition_version,
            definition_digest=model.definition_digest,
            status=RunStatus(model.status),
            current_node=model.current_node,
            context=WorkflowContext.model_validate(model.context),
            version=model.version,
            created_at=model.created_at or datetime.now(timezone.utc),
            updated_at=model.updated_at or datetime.now(timezone.utc),
        )
