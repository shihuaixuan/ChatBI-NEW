"""工具统一返回信封。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolStatus(StrEnum):
    """工具执行终态。

    - success: 正常完成
    - error: 系统/执行错误（可计入重试）
    - denied: 业务/白名单拒绝（默认不计入 SQL retry）
    - interrupted: 取消或挂起中断
    """

    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    INTERRUPTED = "interrupted"


@dataclass
class ToolOutput:
    """双通道返回：summary 进模型上下文，payload 进落库/前端。"""

    success: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    status: ToolStatus | None = None
    offload_ref: str | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = ToolStatus.SUCCESS if self.success else ToolStatus.ERROR
        # 保持 success 与 status 一致，避免调用方只读其一。
        if self.status == ToolStatus.SUCCESS:
            self.success = True
        elif self.success and self.status != ToolStatus.SUCCESS:
            self.success = False

    @classmethod
    def ok(
        cls,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        offload_ref: str | None = None,
    ) -> ToolOutput:
        return cls(
            success=True,
            summary=summary,
            payload=payload or {},
            status=ToolStatus.SUCCESS,
            offload_ref=offload_ref,
        )

    @classmethod
    def denied(
        cls,
        summary: str,
        *,
        error_code: str,
        payload: dict[str, Any] | None = None,
    ) -> ToolOutput:
        return cls(
            success=False,
            summary=summary,
            payload=payload or {},
            error_code=error_code,
            status=ToolStatus.DENIED,
        )

    @classmethod
    def error(
        cls,
        summary: str,
        *,
        error_code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ToolOutput:
        return cls(
            success=False,
            summary=summary,
            payload=payload or {},
            error_code=error_code,
            status=ToolStatus.ERROR,
        )
