"""Workflow Engine 公开的运行数据清理能力。

会话级删除属于业务侧生命周期，但 Run/Node/Checkpoint/Event/Interaction 行的
删除方式由引擎自身定义；业务侧只传入 chat_id 或 run_id 集合。
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlmodel import Session, col, select

from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
    WorkflowRunModel,
)


def list_run_ids_for_chat(session: Session, chat_id: int) -> list[str]:
    """列出归属某会话的全部 Run ID。"""

    return list(
        session.exec(
            select(WorkflowRunModel.run_id).where(WorkflowRunModel.chat_id == chat_id)
        ).all()
    )


def delete_runs(session: Session, run_ids: list[str]) -> None:
    """删除 Run 及其节点执行、检查点、事件与交互请求（不提交事务）。"""

    if not run_ids:
        return
    session.execute(
        delete(NodeExecutionModel).where(col(NodeExecutionModel.run_id).in_(run_ids))
    )
    session.execute(
        delete(WorkflowCheckpointModel).where(
            col(WorkflowCheckpointModel.run_id).in_(run_ids)
        )
    )
    session.execute(
        delete(WorkflowEventModel).where(col(WorkflowEventModel.run_id).in_(run_ids))
    )
    session.execute(
        delete(InteractionRequestModel).where(
            col(InteractionRequestModel.run_id).in_(run_ids)
        )
    )
    session.execute(
        delete(WorkflowRunModel).where(col(WorkflowRunModel.run_id).in_(run_ids))
    )


__all__ = ["delete_runs", "list_run_ids_for_chat"]
