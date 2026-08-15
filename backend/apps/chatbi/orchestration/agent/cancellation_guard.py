"""AgentLoop 和 ToolExecutor 共用的取消检查守卫。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from apps.chatbi.orchestration.agent.cancellation import (
    AgentCancellationRequested,
    CancellationStage,
)
from apps.tool.context import CancellationSignal


@runtime_checkable
class StageAwareCancellation(Protocol):
    """可以读取取消请求并记录观察阶段的运行时接口。"""

    def is_requested(self) -> bool: ...

    def mark_stage(self, stage: CancellationStage) -> None: ...


class CancellationGuard:
    """只检查取消请求并记录阶段，不负责修改 Run 终态。"""

    def __init__(
        self,
        controller: StageAwareCancellation | CancellationSignal,
    ) -> None:
        self._controller = controller

    def check(self, stage: CancellationStage) -> bool:
        """返回是否已取消，并在观察到取消时记录阶段。"""

        if isinstance(self._controller, StageAwareCancellation):
            requested = self._controller.is_requested()
        else:
            requested = self._controller.is_cancelled()
        if not requested:
            return False
        if isinstance(self._controller, StageAwareCancellation):
            self._controller.mark_stage(stage)
        return True

    def require_not_requested(self, stage: CancellationStage) -> None:
        """发现取消请求时抛出明确的内部控制流结果。"""

        if self.check(stage):
            raise AgentCancellationRequested(stage)


__all__ = ["CancellationGuard", "StageAwareCancellation"]
