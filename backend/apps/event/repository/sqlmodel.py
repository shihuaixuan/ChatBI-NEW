"""Event log 的 SQLModel 持久化实现。"""

from datetime import datetime

from sqlalchemy import and_, delete, func, select
from sqlmodel import col

from apps.event.models.orm import EventLog


def next_sequence(session, run_id: int) -> int:
    """返回指定事件流的下一个递增序号。"""

    current = session.exec(
        select(func.max(EventLog.sequence)).where(EventLog.run_id == run_id)
    ).scalar()
    return (current or 0) + 1


def append_event(
    session,
    run_id: int,
    event_type: str,
    payload: dict,
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
    session,
    run_id: int,
    after_sequence: int = 0,
) -> list[EventLog]:
    """按序读取指定序号之后的事件。"""

    stmt = (
        select(EventLog)
        .where(
            and_(
                EventLog.run_id == run_id,
                EventLog.sequence > after_sequence,
            )
        )
        .order_by(EventLog.sequence)
    )
    return session.exec(stmt).scalars().all()


def delete_events_for_runs(session, run_ids: list[int]) -> None:
    """删除一组 Agent run 对应的事件。"""

    if not run_ids:
        return
    session.execute(delete(EventLog).where(col(EventLog.run_id).in_(run_ids)))


# 兼容旧函数名称；新代码统一使用 append_event。
append_trace = append_event

__all__ = [
    "append_event",
    "append_trace",
    "delete_events_for_runs",
    "list_events_after",
    "next_sequence",
]
