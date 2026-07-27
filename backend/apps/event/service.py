"""事件发布服务。"""

from typing import Any

from sqlmodel import Session

from apps.event.models import RenderEvent, create_render_event
from apps.event.repository import sqlmodel as event_repository


class EventPublisher:
    """统一执行事件序号分配、持久化和提交。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(
        self,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        step_id: int | None = None,
    ) -> RenderEvent:
        """提交事件并返回包含持久化序号的渲染事件。"""

        event = event_repository.append_event(
            self._session,
            run_id,
            event_type,
            payload,
            step_id=step_id,
        )
        render_event = create_render_event(
            event_type,
            payload,
            record_id=payload.get("record_id"),
            run_id=run_id,
            sequence=event.sequence,
            step_id=step_id,
        )
        # Event log 同步保存新契约字段，旧 payload 字段仍保持平铺兼容。
        event.payload = {
            **payload,
            "kind": render_event.kind,
            "phase": render_event.phase,
            "domain": render_event.domain,
            **({"block_id": render_event.block_id} if render_event.block_id else {}),
        }
        self._session.commit()
        return render_event


__all__ = ["EventPublisher"]
