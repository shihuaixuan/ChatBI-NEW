from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, func, select

from sqlbot_platform.workflow_engine.domain.event import WorkflowEvent
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    WorkflowEventModel,
)

PUBLIC_PAYLOAD_ALLOWLIST = frozenset(
    {
        "answer",
        "error_code",
        "interaction_id",
        "label",
        "message",
        "node",
        "node_name",
        "pending_interaction",
        "progress",
        "question",
        "reason_code",
        "status",
        "summary",
    }
)


@dataclass(frozen=True)
class PublishResult:
    """一次 Outbox 发布扫描的结果。"""

    published: int
    failed: int


class EventOutbox:
    """数据库事件 Outbox。

    Outbox 使用 `event_id` 做幂等键，使用 `(run_id, sequence)` 固定 Run 内顺序。
    公开载荷在写入时先经过白名单过滤，内部诊断信息只进入 `internal_payload`，
    不会被发布器或前端续传接口直接读取。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: WorkflowEvent, internal_payload: dict[str, Any] | None = None) -> WorkflowEvent:
        existing = self._session.exec(
            select(WorkflowEventModel).where(WorkflowEventModel.event_id == event.event_id)
        ).one_or_none()
        if existing is not None:
            return self._to_public_event(existing)

        self._ensure_monotonic_sequence(event)
        model = WorkflowEventModel(
            event_id=event.event_id,
            run_id=event.run_id,
            sequence=event.sequence,
            event_type=event.event_type,
            node_name=event.node_name,
            node_execution_id=event.node_execution_id,
            public_payload=_sanitize_public_payload(event.public_payload),
            internal_payload=internal_payload or {},
            internal_payload_ref=event.internal_payload_ref,
            publish_status="pending",
            publish_attempts=0,
            created_at=event.created_at,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_public_event(model)

    def publish_pending(
        self,
        publisher: Callable[[WorkflowEvent], None],
        limit: int = 100,
        run_id: str | None = None,
    ) -> PublishResult:
        statement = select(WorkflowEventModel).where(
            WorkflowEventModel.publish_status == "pending"
        )
        if run_id is not None:
            # 测试、补偿任务可限定单个 Run，避免消费其他运行的待发布事件。
            statement = statement.where(WorkflowEventModel.run_id == run_id)
        models = self._session.exec(
            statement.order_by(WorkflowEventModel.sequence).limit(limit)
        ).all()
        published = 0
        failed = 0
        for model in models:
            model.publish_attempts += 1
            try:
                publisher(self._to_public_event(model))
            except Exception:
                # 发布失败时保持 pending，下一轮扫描可继续重试；异常细节不落公开事件。
                failed += 1
            else:
                model.publish_status = "published"
                published += 1
            self._session.add(model)
        self._session.flush()
        return PublishResult(published=published, failed=failed)

    def _ensure_monotonic_sequence(self, event: WorkflowEvent) -> None:
        latest_sequence = self._session.exec(
            select(func.max(col(WorkflowEventModel.sequence))).where(WorkflowEventModel.run_id == event.run_id)
        ).one()
        if latest_sequence is not None and event.sequence <= latest_sequence:
            raise ValueError(
                f"EVENT_SEQUENCE_NOT_MONOTONIC: run_id={event.run_id}, "
                f"latest={latest_sequence}, current={event.sequence}"
            )

    def _to_public_event(self, model: WorkflowEventModel) -> WorkflowEvent:
        return WorkflowEvent(
            event_id=model.event_id,
            run_id=model.run_id,
            sequence=model.sequence,
            event_type=model.event_type,
            node_name=model.node_name,
            node_execution_id=model.node_execution_id,
            public_payload=_sanitize_public_payload(model.public_payload),
            internal_payload_ref=None,
            created_at=model.created_at or datetime.now(timezone.utc),
        )


def _sanitize_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留可公开给前端和 SSE 客户端的字段。"""

    return {key: value for key, value in payload.items() if key in PUBLIC_PAYLOAD_ALLOWLIST}
