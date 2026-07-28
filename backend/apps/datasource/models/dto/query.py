"""Datasource 安全查询请求、策略和结果模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DatasourceQueryStatus(StrEnum):
    """Datasource 查询终态。"""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class DatasourceQueryErrorCategory(StrEnum):
    """查询失败的稳定类别。"""

    AUTHORIZATION = "authorization"
    SAFETY = "safety"
    VALIDATION = "validation"
    DOMAIN = "domain"
    TRANSIENT = "transient"
    CONFIGURATION = "configuration"
    TIMEOUT = "timeout"


class DatasourceQueryRetryAdvice(StrEnum):
    """查询调用方的重试建议。"""

    NEVER = "never"
    SAME_INPUT = "same_input"
    CORRECT_INPUT = "correct_input"


class DatasourceQuerySubject(BaseModel):
    """服务端确认的查询身份。"""

    model_config = ConfigDict(frozen=True)

    user_id: int = Field(gt=0)
    workspace_id: int = Field(gt=0)


class DatasourceRowFilter(BaseModel):
    table: str = Field(min_length=1)
    condition: str = Field(min_length=1)


class DatasourceDeniedColumn(BaseModel):
    table: str = Field(min_length=1)
    column: str = Field(min_length=1)


class DatasourceQueryPolicy(BaseModel):
    """Access Control 提供的明确查询范围。"""

    allowed: bool = True
    reason: str = "permission_applied"
    error_code: str | None = None
    authorized_tables: list[str] = Field(default_factory=list)
    row_filters: list[DatasourceRowFilter] = Field(default_factory=list)
    denied_columns: list[DatasourceDeniedColumn] = Field(default_factory=list)


class DatasourceQueryRequest(BaseModel):
    """统一安全查询入口的请求。"""

    model_config = ConfigDict(frozen=True)

    sql: str
    datasource_id: int = Field(gt=0)
    subject: DatasourceQuerySubject
    selected_tables: list[str] = Field(default_factory=list)
    deadline_monotonic: float | None = None


class DatasourceQueryData(BaseModel):
    """校验或执行成功后返回的数据。"""

    sql: str
    tables: list[str] = Field(default_factory=list)
    effective_tables: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    stats_summary: dict[str, dict[str, float]] = Field(default_factory=dict)
    full_data: list[dict[str, Any]] = Field(default_factory=list)
    execution_ms: int = 0
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class DatasourceQueryResult(BaseModel):
    """不依赖 Agent Tool 的 Datasource 查询结果。"""

    status: DatasourceQueryStatus
    data: DatasourceQueryData | None = None
    error_code: str | None = None
    message: str = ""
    error_category: DatasourceQueryErrorCategory | None = None
    retry_advice: DatasourceQueryRetryAdvice = DatasourceQueryRetryAdvice.NEVER
    retry_count: int = 0

    @classmethod
    def succeeded(cls, data: DatasourceQueryData) -> DatasourceQueryResult:
        return cls(status=DatasourceQueryStatus.SUCCEEDED, data=data)

    @classmethod
    def rejected(
        cls,
        message: str,
        *,
        error_code: str,
        error_category: DatasourceQueryErrorCategory,
    ) -> DatasourceQueryResult:
        return cls(
            status=DatasourceQueryStatus.REJECTED,
            error_code=error_code,
            message=message,
            error_category=error_category,
        )

    @classmethod
    def failed(
        cls,
        message: str,
        *,
        error_code: str,
        error_category: DatasourceQueryErrorCategory,
        retry_advice: DatasourceQueryRetryAdvice,
    ) -> DatasourceQueryResult:
        return cls(
            status=DatasourceQueryStatus.FAILED,
            error_code=error_code,
            message=message,
            error_category=error_category,
            retry_advice=retry_advice,
        )


class DatasourceDriverResult(BaseModel):
    """驱动执行器归一化结果。"""

    succeeded: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str = ""
    transient: bool = False
    timed_out: bool = False


__all__ = [
    "DatasourceDeniedColumn",
    "DatasourceDriverResult",
    "DatasourceQueryData",
    "DatasourceQueryErrorCategory",
    "DatasourceQueryPolicy",
    "DatasourceQueryRequest",
    "DatasourceQueryResult",
    "DatasourceQueryRetryAdvice",
    "DatasourceQueryStatus",
    "DatasourceQuerySubject",
    "DatasourceRowFilter",
]
