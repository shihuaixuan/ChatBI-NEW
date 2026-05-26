from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

_AFTER_COMMIT_CALLBACKS = "semantic_after_commit_callbacks"


def run_after_commit(session: Session, callback: Callable[[], None]) -> None:
    callbacks = session.info.setdefault(_AFTER_COMMIT_CALLBACKS, [])
    callbacks.append(callback)


@event.listens_for(Session, "after_commit")
def _run_after_commit_callbacks(session: Session) -> None:
    callbacks = session.info.pop(_AFTER_COMMIT_CALLBACKS, [])
    for callback in callbacks:
        callback()


@event.listens_for(Session, "after_rollback")
def _clear_after_commit_callbacks(session: Session) -> None:
    session.info.pop(_AFTER_COMMIT_CALLBACKS, None)
