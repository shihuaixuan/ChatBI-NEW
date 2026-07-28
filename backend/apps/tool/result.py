"""Tool 统一结构化结果。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

ResultT = TypeVar("ResultT", bound=BaseModel)


class EmptyToolData(BaseModel):
    """没有业务数据的成功结果。"""

    model_config = ConfigDict(extra="forbid")


class ToolStatus(StrEnum):
    """Tool 的唯一终态。"""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ToolErrorCategory(StrEnum):
    """宿主用于决定处置方式的稳定错误类别。"""

    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    SAFETY = "safety"
    BUSINESS_RULE = "business_rule"
    DOMAIN = "domain"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    CONFIGURATION = "configuration"


class RetryAdvice(StrEnum):
    """Tool 对宿主和模型提供的重试建议。"""

    NEVER = "never"
    SAME_INPUT = "same_input"
    CORRECT_INPUT = "correct_input"


@dataclass(frozen=True, slots=True)
class ToolResult(Generic[ResultT]):
    """模型内容、业务数据和内部元数据相互隔离的结果信封。"""

    status: ToolStatus
    model_content: str
    data: ResultT | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_category: ToolErrorCategory | None = None
    retry_advice: RetryAdvice = RetryAdvice.NEVER
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def succeeded(
        cls,
        model_content: str,
        data: ResultT,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult[ResultT]:
        return cls(
            status=ToolStatus.SUCCEEDED,
            model_content=model_content,
            data=data,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def rejected(
        cls,
        model_content: str,
        *,
        error_code: str,
        error_category: ToolErrorCategory,
        details: dict[str, Any] | None = None,
    ) -> ToolResult[Any]:
        return cls(
            status=ToolStatus.REJECTED,
            model_content=model_content,
            error_code=error_code,
            error_category=error_category,
            retry_advice=RetryAdvice.NEVER,
            details=dict(details or {}),
        )

    @classmethod
    def failed(
        cls,
        model_content: str,
        *,
        error_code: str,
        error_category: ToolErrorCategory,
        retry_advice: RetryAdvice,
        details: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult[Any]:
        return cls(
            status=ToolStatus.FAILED,
            model_content=model_content,
            metadata=dict(metadata or {}),
            error_code=error_code,
            error_category=error_category,
            retry_advice=retry_advice,
            details=dict(details or {}),
        )

    @classmethod
    def interrupted(
        cls,
        model_content: str,
        *,
        error_code: str,
        error_category: ToolErrorCategory = ToolErrorCategory.CANCELLATION,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult[Any]:
        return cls(
            status=ToolStatus.INTERRUPTED,
            model_content=model_content,
            metadata=dict(metadata or {}),
            error_code=error_code,
            error_category=error_category,
            retry_advice=RetryAdvice.NEVER,
        )

    def with_updates(self, **changes: Any) -> ToolResult[ResultT]:
        """返回更新后的不可变结果。"""

        return replace(self, **changes)


__all__ = [
    "EmptyToolData",
    "RetryAdvice",
    "ToolErrorCategory",
    "ToolResult",
    "ToolStatus",
]
