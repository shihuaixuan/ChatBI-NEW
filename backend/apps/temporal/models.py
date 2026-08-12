"""一次问数 Run 内不可变的时间解释上下文。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WeekStart: TypeAlias = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class TemporalContext(BaseModel):
    """固定相对时间解释所需的全部基础配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_at: datetime
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    week_start: WeekStart = "monday"
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    fiscal_year_label: Literal["start_year", "end_year"] = "start_year"
    business_calendar_id: str | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """时区必须是可解析的 IANA 名称。"""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("TEMPORAL_TIMEZONE_INVALID") from exc
        return value

    @model_validator(mode="after")
    def validate_reference_at(self) -> TemporalContext:
        """禁止无时区基准时间进入 Run，避免部署环境改变解释结果。"""

        if self.reference_at.tzinfo is None or self.reference_at.utcoffset() is None:
            raise ValueError("TEMPORAL_REFERENCE_AT_TIMEZONE_REQUIRED")
        return self

    @property
    def reference_date(self) -> date:
        """按业务时区返回本 Run 的固定基准日期。"""

        return self.reference_at.astimezone(ZoneInfo(self.timezone)).date()


def build_temporal_context(
    *,
    reference_at: datetime | None = None,
    timezone: str = "Asia/Shanghai",
    locale: str = "zh-CN",
    week_start: WeekStart = "monday",
    fiscal_year_start_month: int = 1,
    fiscal_year_label: Literal["start_year", "end_year"] = "start_year",
    business_calendar_id: str | None = None,
) -> TemporalContext:
    """在 Run 创建边界只调用一次，生成可持久化的时间上下文。"""

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("TEMPORAL_TIMEZONE_INVALID") from exc
    fixed_reference_at = reference_at or datetime.now(zone)
    if fixed_reference_at.tzinfo is None or fixed_reference_at.utcoffset() is None:
        raise ValueError("TEMPORAL_REFERENCE_AT_TIMEZONE_REQUIRED")
    return TemporalContext(
        reference_at=fixed_reference_at.astimezone(zone),
        timezone=timezone,
        locale=locale,
        week_start=week_start,
        fiscal_year_start_month=fiscal_year_start_month,
        fiscal_year_label=fiscal_year_label,
        business_calendar_id=business_calendar_id,
    )


def build_run_temporal_context(
    *,
    reference_at: datetime | None = None,
) -> TemporalContext:
    """使用统一企业配置创建 Run 时间上下文，调用方不得各自读取环境变量。"""

    from common.core.config import settings

    return build_temporal_context(
        reference_at=reference_at,
        timezone=settings.TEMPORAL_TIMEZONE,
        locale=settings.TEMPORAL_LOCALE,
        week_start=settings.TEMPORAL_WEEK_START,
        fiscal_year_start_month=settings.TEMPORAL_FISCAL_YEAR_START_MONTH,
        fiscal_year_label=settings.TEMPORAL_FISCAL_YEAR_LABEL,
        business_calendar_id=settings.TEMPORAL_BUSINESS_CALENDAR_ID,
    )


__all__ = [
    "TemporalContext",
    "WeekStart",
    "build_run_temporal_context",
    "build_temporal_context",
]
