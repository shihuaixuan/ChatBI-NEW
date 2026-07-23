"""Workflow Engine 持久化运行组件的公开装配入口。"""

from dataclasses import dataclass

from sqlmodel import Session

from apps.workflow_engine.infrastructure.events.publisher import (
    DatabaseEventPublisher,
)
from apps.workflow_engine.infrastructure.persistence.interaction_manager import (
    DatabaseInteractionManager,
)
from apps.workflow_engine.infrastructure.persistence.node_execution_repository import (
    NodeExecutionRepository,
)
from apps.workflow_engine.infrastructure.persistence.run_repository import RunRepository
from apps.workflow_engine.ports.event_publisher import EventPublisher
from apps.workflow_engine.ports.run_store import RunStore
from apps.workflow_engine.ports.runtime_persistence import (
    InteractionStore,
    NodeExecutionRecorder,
)


@dataclass(frozen=True, slots=True)
class PersistentRuntimeServices:
    """业务图装配运行时所需的稳定端口集合。"""

    run_store: RunStore
    event_publisher: EventPublisher
    interaction_store: InteractionStore
    node_execution_recorder: NodeExecutionRecorder


def build_persistent_runtime_services(
    session: Session,
    *,
    commit_events: bool = False,
    run_store: RunStore | None = None,
) -> PersistentRuntimeServices:
    """隐藏数据库实现，业务图只依赖 Workflow Engine 公开端口。"""

    return PersistentRuntimeServices(
        run_store=run_store if run_store is not None else RunRepository(session),
        event_publisher=DatabaseEventPublisher(
            session,
            commit_on_publish=commit_events,
        ),
        interaction_store=DatabaseInteractionManager(session),
        node_execution_recorder=NodeExecutionRepository(session),
    )


__all__ = [
    "PersistentRuntimeServices",
    "build_persistent_runtime_services",
]
