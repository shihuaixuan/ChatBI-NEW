"""Temporal 模块公共错误。"""

from __future__ import annotations


class TemporalError(ValueError):
    """时间契约、校验或解析失败的公共基类。"""

    code = "TEMPORAL_ERROR"

    def __init__(self, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(self.code)


class TemporalPlanValidationError(TemporalError):
    """时间计划不满足跨字段业务约束。"""

    code = "TEMPORAL_PLAN_INVALID"


class TemporalPlanResolutionError(TemporalError):
    """时间计划无法转换为可信绝对范围。"""

    code = "TEMPORAL_PLAN_RESOLUTION_FAILED"


__all__ = [
    "TemporalError",
    "TemporalPlanResolutionError",
    "TemporalPlanValidationError",
]
