"""PLAN 模式占位。真实多节点规划在 P1-3 实现。"""

from typing import NoReturn


class PlanModeNotReadyError(NotImplementedError):
    """当前版本尚未提供 PLAN 模式执行器。"""


class PlanPipeline:
    def run(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise PlanModeNotReadyError("PLAN_MODE_P1_3_NOT_READY")


__all__ = ["PlanModeNotReadyError", "PlanPipeline"]
