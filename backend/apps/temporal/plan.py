"""模型时间理解与服务端确定性解析之间的严格时间计划契约。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.temporal.errors import TemporalPlanValidationError

TemporalExpressionRole: TypeAlias = Literal["query_filter", "metric_definition"]
TemporalExpressionSource: TypeAlias = Literal[
    "rewritten_question",
    "user_confirmation",
]
TemporalAmbiguityCode: TypeAlias = Literal[
    "time_range_amount_missing",
    "time_range_unit_missing",
    "time_range_conflict",
    "time_role_ambiguous",
    "time_calendar_ambiguous",
    "time_reference_inclusion_ambiguous",
    "time_expression_unsupported",
]
TemporalGroupingGrain: TypeAlias = Literal[
    "day",
    "week",
    "month",
    "quarter",
    "year",
]
TemporalRollingUnit: TypeAlias = Literal["day", "week", "month", "year"]
TemporalPeriodUnit: TypeAlias = Literal["week", "month", "quarter", "year"]


class TemporalExpressionBase(BaseModel):
    """所有时间表达共享的可验证来源和业务作用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw: str = Field(min_length=1)
    source: TemporalExpressionSource = "rewritten_question"
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=1)
    role: TemporalExpressionRole

    @model_validator(mode="after")
    def validate_source_span(self) -> TemporalExpressionBase:
        """位置字段必须成对出现，并形成合法左闭右开范围。"""

        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("TEMPORAL_EXPRESSION_SOURCE_SPAN_INCOMPLETE")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.start_offset >= self.end_offset
        ):
            raise ValueError("TEMPORAL_EXPRESSION_SOURCE_SPAN_INVALID")
        return self


class AbsoluteDateExpression(TemporalExpressionBase):
    kind: Literal["absolute_date"]
    date: date


class AbsoluteRangeExpression(TemporalExpressionBase):
    kind: Literal["absolute_range"]
    start: date
    end_inclusive: date

    @model_validator(mode="after")
    def validate_range(self) -> AbsoluteRangeExpression:
        if self.start > self.end_inclusive:
            raise ValueError("TEMPORAL_ABSOLUTE_RANGE_INVALID")
        return self


class RelativeDateExpression(TemporalExpressionBase):
    kind: Literal["relative_date"]
    offset_days: int


class RollingRangeExpression(TemporalExpressionBase):
    kind: Literal["rolling_range"]
    direction: Literal["past", "future"]
    amount: int = Field(gt=0)
    unit: TemporalRollingUnit
    include_reference_date: bool


class CalendarPeriodExpression(TemporalExpressionBase):
    kind: Literal["calendar_period"]
    unit: TemporalPeriodUnit
    offset: int = 0


class FiscalPeriodExpression(TemporalExpressionBase):
    kind: Literal["fiscal_period"]
    unit: Literal["year", "quarter"]
    offset: int = 0
    fiscal_year: int | None = Field(default=None, ge=1, le=9999)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)

    @model_validator(mode="after")
    def validate_fiscal_selector(self) -> FiscalPeriodExpression:
        """相对财政周期和指定财年互斥，财季必须完整指定。"""

        if self.fiscal_year is None:
            if self.fiscal_quarter is not None:
                raise ValueError("TEMPORAL_FISCAL_YEAR_REQUIRED")
            return self
        if self.offset != 0:
            raise ValueError("TEMPORAL_FISCAL_NAMED_OFFSET_CONFLICT")
        if self.unit == "year" and self.fiscal_quarter is not None:
            raise ValueError("TEMPORAL_FISCAL_QUARTER_FORBIDDEN")
        if self.unit == "quarter" and self.fiscal_quarter is None:
            raise ValueError("TEMPORAL_FISCAL_QUARTER_REQUIRED")
        return self


TemporalExpression: TypeAlias = Annotated[
    AbsoluteDateExpression
    | AbsoluteRangeExpression
    | RelativeDateExpression
    | RollingRangeExpression
    | CalendarPeriodExpression
    | FiscalPeriodExpression,
    Field(discriminator="kind"),
]


class TemporalGrouping(BaseModel):
    """用户明确要求的时间分组粒度，不包含时间维度资产。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grain: TemporalGroupingGrain


class TemporalAmbiguity(BaseModel):
    """模型识别出的业务歧义，展示文案由服务端生成。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: TemporalAmbiguityCode
    raw: str = Field(min_length=1)


class TemporalPlan(BaseModel):
    """模型时间任务经过结构校验后的第一版权威计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    status: Literal[
        "no_time",
        "resolved",
        "clarification_required",
        "unsupported",
    ]
    expressions: tuple[TemporalExpression, ...] = ()
    grouping: TemporalGrouping | None = None
    comparison: None = None
    ambiguities: tuple[TemporalAmbiguity, ...] = ()
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_status_payload(self) -> TemporalPlan:
        """状态必须和计划内容一致，禁止空计划伪装成可执行结果。"""

        if self.status == "no_time":
            if self.expressions or self.grouping is not None or self.ambiguities:
                raise ValueError("TEMPORAL_NO_TIME_PAYLOAD_CONFLICT")
        elif self.status == "resolved":
            if not self.expressions and self.grouping is None:
                raise ValueError("TEMPORAL_RESOLVED_PAYLOAD_REQUIRED")
            if self.ambiguities:
                raise ValueError("TEMPORAL_RESOLVED_AMBIGUITY_FORBIDDEN")
        elif not self.ambiguities:
            raise ValueError("TEMPORAL_AMBIGUITY_REQUIRED")
        return self


class ResolvedTemporalRange(BaseModel):
    """服务端确定性生成、可进入时间维度绑定的绝对范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["absolute_range"] = "absolute_range"
    start: date
    end_exclusive: date
    timezone: str
    source_raw: str = Field(min_length=1)
    role: Literal["query_filter"] = "query_filter"
    calendar: Literal["natural", "fiscal"] = "natural"
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    fiscal_year_label: Literal["start_year", "end_year"] | None = None

    @model_validator(mode="after")
    def validate_absolute_range(self) -> ResolvedTemporalRange:
        if self.start >= self.end_exclusive:
            raise ValueError("TEMPORAL_RESOLVED_RANGE_INVALID")
        fiscal_fields = (
            self.fiscal_year,
            self.fiscal_quarter,
            self.fiscal_year_start_month,
            self.fiscal_year_label,
        )
        if self.calendar == "natural" and any(
            value is not None for value in fiscal_fields
        ):
            raise ValueError("TEMPORAL_NATURAL_RANGE_FISCAL_METADATA_FORBIDDEN")
        if self.calendar == "fiscal" and (
            self.fiscal_year is None
            or self.fiscal_year_start_month is None
            or self.fiscal_year_label is None
        ):
            raise ValueError("TEMPORAL_FISCAL_RANGE_METADATA_REQUIRED")
        return self


class ResolvedTemporalPlan(BaseModel):
    """经过业务校验和确定性日期计算的时间执行计划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    status: Literal["no_time", "resolved"]
    filters: tuple[ResolvedTemporalRange, ...] = ()
    grouping: TemporalGrouping | None = None

    @model_validator(mode="after")
    def validate_resolved_payload(self) -> ResolvedTemporalPlan:
        if self.status == "no_time" and (self.filters or self.grouping is not None):
            raise ValueError("TEMPORAL_RESOLVED_NO_TIME_PAYLOAD_CONFLICT")
        return self


def validate_temporal_plan(
    plan: TemporalPlan,
    *,
    rewritten_question: str | None = None,
    user_confirmation: str | None = None,
    metric_mentions: tuple[str, ...] = (),
) -> TemporalPlan:
    """集中校验表达来源、指标内部时间和单一查询时间范围不变量。"""

    source_texts = {
        "rewritten_question": rewritten_question,
        "user_confirmation": user_confirmation,
    }
    for expression in plan.expressions:
        source_text = source_texts[expression.source]
        if source_text is None:
            continue
        if expression.start_offset is None or expression.end_offset is None:
            if expression.raw not in source_text:
                raise TemporalPlanValidationError(
                    "TEMPORAL_EXPRESSION_SOURCE_TEXT_MISMATCH"
                )
            continue
        if (
            source_text[expression.start_offset : expression.end_offset]
            != expression.raw
        ):
            raise TemporalPlanValidationError(
                "TEMPORAL_EXPRESSION_SOURCE_SPAN_MISMATCH"
            )

    if metric_mentions:
        metric_definition_expressions = {
            expression.raw
            for expression in plan.expressions
            if any(
                expression.raw != metric and expression.raw in metric
                for metric in metric_mentions
            )
        }
        for expression in plan.expressions:
            if (
                expression.raw in metric_definition_expressions
                and expression.role != "metric_definition"
            ):
                raise TemporalPlanValidationError(
                    "TEMPORAL_METRIC_DEFINITION_ROLE_REQUIRED"
                )
            if (
                expression.role == "metric_definition"
                and expression.raw not in metric_definition_expressions
            ):
                raise TemporalPlanValidationError(
                    "TEMPORAL_METRIC_DEFINITION_SOURCE_REQUIRED"
                )
        if any(
            any(ambiguity.raw in metric for metric in metric_mentions)
            for ambiguity in plan.ambiguities
        ):
            raise TemporalPlanValidationError(
                "TEMPORAL_METRIC_DEFINITION_AMBIGUITY_FORBIDDEN"
            )

    query_filters = [
        expression
        for expression in plan.expressions
        if expression.role == "query_filter"
    ]
    if plan.status == "resolved" and len(query_filters) > 1:
        raise TemporalPlanValidationError("TEMPORAL_PLAN_QUERY_FILTER_CONFLICT")
    return plan


__all__ = [
    "AbsoluteDateExpression",
    "AbsoluteRangeExpression",
    "CalendarPeriodExpression",
    "FiscalPeriodExpression",
    "RelativeDateExpression",
    "ResolvedTemporalPlan",
    "ResolvedTemporalRange",
    "RollingRangeExpression",
    "TemporalAmbiguity",
    "TemporalAmbiguityCode",
    "TemporalExpression",
    "TemporalGrouping",
    "TemporalPlan",
    "validate_temporal_plan",
]
