"""Agent Run 的跨请求取消控制器。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlmodel import Session, select

from apps.chatbi.models import AgentRunStatus, ChatbiAgentRun


class CancellationStage(StrEnum):
    """记录取消请求被运行时观察到的阶段。"""

    BEFORE_LLM = "before_llm"
    DURING_LLM = "during_llm"
    AFTER_LLM = "after_llm"
    BEFORE_TOOL = "before_tool"
    DURING_TOOL = "during_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_NEXT_STEP = "before_next_step"


class AgentCancellationRequested(Exception):
    """内部取消结果，供编排层停止当前控制流。"""

    def __init__(self, stage: CancellationStage) -> None:
        self.stage = stage
        super().__init__(f"AGENT_CANCEL_REQUESTED:{stage.value}")


class DatabaseCancellationController:
    """通过独立数据库会话读取和记录取消请求。"""

    def __init__(self, engine: Any, run_id: int) -> None:
        self._engine = engine
        self._run_id = run_id

    def _read_run(self) -> ChatbiAgentRun | None:
        # 每次查询都使用独立 Session，避免与主执行事务或并行工具共享连接。
        with Session(self._engine) as session:
            return session.exec(
                select(ChatbiAgentRun).where(ChatbiAgentRun.id == self._run_id)
            ).one_or_none()

    def is_requested(self) -> bool:
        run = self._read_run()
        return bool(
            run
            and run.status
            in {
                AgentRunStatus.CANCEL_REQUESTED.value,
                AgentRunStatus.CANCELLED.value,
            }
        )

    def requested_at(self) -> datetime | None:
        run = self._read_run()
        return run.cancel_requested_at if run is not None else None

    def mark_stage(self, stage: CancellationStage) -> None:
        """仅在取消已请求或已收口时记录观察阶段。"""

        with Session(self._engine) as session:
            run = session.exec(
                select(ChatbiAgentRun).where(ChatbiAgentRun.id == self._run_id)
            ).one_or_none()
            if run is None:
                raise KeyError(f"AGENT_RUN_NOT_FOUND:{self._run_id}")
            if run.status not in {
                AgentRunStatus.CANCEL_REQUESTED.value,
                AgentRunStatus.CANCELLED.value,
            }:
                return
            run.cancel_stage = stage.value
            run.updated_at = datetime.utcnow()
            session.add(run)
            session.commit()


class DatabaseRunCancellationSignal:
    """保留给工具运行时使用的取消信号适配器。"""

    def __init__(self, engine: Any, run_id: int) -> None:
        self._controller = DatabaseCancellationController(engine, run_id)

    def is_cancelled(self) -> bool:
        return self._controller.is_requested()

    def is_requested(self) -> bool:
        return self._controller.is_requested()

    def mark_stage(self, stage: CancellationStage) -> None:
        self._controller.mark_stage(stage)


__all__ = [
    "AgentCancellationRequested",
    "CancellationStage",
    "DatabaseCancellationController",
    "DatabaseRunCancellationSignal",
]
