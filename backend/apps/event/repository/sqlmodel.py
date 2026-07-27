"""Event log 的 SQLModel 持久化实现。"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from apps.event.models.orm import EventLog


def next_sequence(session: Session, run_id: int) -> int:
    """返回指定事件流的下一个递增序号。"""

    current = session.exec(
        select(func.max(col(EventLog.sequence))).where(col(EventLog.run_id) == run_id)
    ).one()
    return (current or 0) + 1


def append_event(
    session: Session,
    run_id: int,
    event_type: str,
    payload: dict[str, Any],
    step_id: int | None = None,
) -> EventLog:
    """追加事件；事务提交由调用方控制。"""

    event = EventLog(
        run_id=run_id,
        step_id=step_id,
        sequence=next_sequence(session, run_id),
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(),
    )
    session.add(event)
    session.flush()
    return event


def list_events_after(
    session: Session,
    run_id: int,
    after_sequence: int = 0,
) -> list[EventLog]:
    """按序读取指定序号之后的事件。"""

    stmt = (
        select(EventLog)
        .where(
            col(EventLog.run_id) == run_id,
            col(EventLog.sequence) > after_sequence,
        )
        .order_by(col(EventLog.sequence))
    )
    return list(session.exec(stmt).all())


def delete_events_for_runs(session: Session, run_ids: list[int]) -> None:
    """删除一组 Agent run 对应的事件。"""

    if not run_ids:
        return
    session.execute(delete(EventLog).where(col(EventLog.run_id).in_(run_ids)))

__all__ = [
    "append_event",
    "delete_events_for_runs",
    "list_events_after",
    "next_sequence",
]
