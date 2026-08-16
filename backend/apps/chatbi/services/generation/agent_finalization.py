"""Agent 查询完成后的分析回复和图表配置生成。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from apps.chatbi.errors import AgentFinalizationError, QuestionModelError
from apps.chatbi.models import QuestionModelInvocationData, QuestionModelJSONMode
from apps.chatbi.services.understanding.model_invocation import StructuredModelService


@dataclass(frozen=True, slots=True)
class AgentFinalizationInput:
    """分析回复和图表模型共享的查询结果输入。"""

    question: str
    intent: dict[str, Any]
    execution: dict[str, Any]
    rows: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AgentFinalizationResult:
    """两个模型任务的最终输出。"""

    answer: str
    chart: dict[str, Any]


class _AnswerModelOutput(BaseModel):
    answer: str = Field(min_length=1)


class _ChartModelOutput(BaseModel):
    type: Literal["none", "table", "bar", "line", "pie"]
    title: str = ""
    x_field: str | None = None
    y_fields: list[str] = Field(default_factory=list)


class AgentFinalizationService:
    """用两个独立结构化模型任务完成最终回答和图表配置。"""

    def __init__(self, model_service: StructuredModelService) -> None:
        self._model_service = model_service

    def generate(self, data: AgentFinalizationInput) -> AgentFinalizationResult:
        self._validate_input(data)
        context = _build_context(data)
        answer = self._generate_answer(context)
        chart = self._generate_chart(context, data.execution, data.rows)
        return AgentFinalizationResult(answer=answer, chart=chart)

    def _generate_answer(self, context: dict[str, Any]) -> str:
        payload = self._invoke_model(
            stage="agent_answer_generation",
            system_prompt=_ANSWER_SYSTEM_PROMPT,
            context=context,
        )
        try:
            result = _AnswerModelOutput.model_validate(payload)
        except ValidationError as exc:
            raise AgentFinalizationError(
                "AGENT_ANSWER_OUTPUT_INVALID",
                "分析回复模型返回的内容不符合契约。",
            ) from exc
        return result.answer.strip()

    def _generate_chart(
        self,
        context: dict[str, Any],
        execution: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = self._invoke_model(
            stage="agent_chart_generation",
            system_prompt=_CHART_SYSTEM_PROMPT,
            context=context,
        )
        try:
            result = _ChartModelOutput.model_validate(payload)
        except ValidationError as exc:
            raise AgentFinalizationError(
                "AGENT_CHART_OUTPUT_INVALID",
                "图表配置模型返回的内容不符合契约。",
            ) from exc

        if result.type == "none" or not rows:
            return {}

        fields = [str(field) for field in execution.get("fields") or []]
        allowed_fields = set(fields)
        selected_fields = [result.x_field, *result.y_fields]
        invalid_fields = [
            field
            for field in selected_fields
            if field is not None and field not in allowed_fields
        ]
        if invalid_fields:
            raise AgentFinalizationError(
                "AGENT_CHART_FIELD_INVALID",
                f"图表配置使用了查询结果之外的字段：{invalid_fields}。",
            )

        if result.type == "table":
            return {
                "type": "table",
                "title": result.title,
                "columns": [{"name": field, "value": field} for field in fields],
            }

        if not result.x_field or not result.y_fields:
            raise AgentFinalizationError(
                "AGENT_CHART_AXIS_REQUIRED",
                "非表格图表必须指定分类字段和指标字段。",
            )

        return {
            "type": result.type,
            "title": result.title,
            "x": result.x_field,
            "y": result.y_fields,
        }

    def _invoke_model(
        self,
        *,
        stage: str,
        system_prompt: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._model_service.invoke(
                QuestionModelInvocationData(
                    stage=stage,
                    system_prompt=system_prompt,
                    user_prompt=(
                        "请基于以下真实查询结果完成任务，并严格返回 JSON：\n"
                        + json.dumps(context, ensure_ascii=False, sort_keys=True)
                    ),
                    json_mode=QuestionModelJSONMode.STRICT,
                )
            ).payload
        except QuestionModelError as exc:
            raise AgentFinalizationError(
                f"{stage.upper()}_FAILED",
                f"{stage}执行失败。",
            ) from exc

    @staticmethod
    def _validate_input(data: AgentFinalizationInput) -> None:
        if not data.question.strip():
            raise AgentFinalizationError(
                "AGENT_FINALIZATION_QUESTION_REQUIRED",
                "生成最终回答需要用户问题。",
            )
        if data.execution.get("status") not in {None, "succeeded"}:
            raise AgentFinalizationError(
                "AGENT_FINALIZATION_EXECUTION_FAILED",
                "SQL 未成功执行，不能生成最终回答。",
            )
        if any(not isinstance(row, dict) for row in data.rows):
            raise AgentFinalizationError(
                "AGENT_FINALIZATION_ROWS_INVALID",
                "SQL 返回结果不是对象行列表。",
            )


_ANSWER_SYSTEM_PROMPT = """
你是 ChatBI 的数据分析回复模型。
你只能基于输入中的真实查询结果回答，不得编造任何字段、数字、趋势或原因。
请生成有分析价值的简洁中文回复，而不是机械重复整张数据表：
- 根据用户问题和分析意图总结结果；
- 能比较时说明最高、最低、差异或变化；
- 能计算比例或变化率时必须基于输入数据计算；
- 数据不足以得出结论时明确说明，不要猜测；
- 不要输出 SQL、内部字段说明或模型推理过程；
- 只输出一个 JSON 对象：{"answer":"用户可见的分析回复"}。
""".strip()


_CHART_SYSTEM_PROMPT = """
你是 ChatBI 的图表配置模型。
请根据用户问题、分析意图、查询字段和真实数据选择最合适的展示方式。
只允许使用 fields 中存在的字段，不能改写字段名或创造字段。
当结果包含一个分类字段和一个数值字段，且用户要求对比或排行时，优先使用 type="bar"；
只有数据不适合图表展示时才使用 type="table"。
没有合适图表、数据为空或字段不足时返回 type="none"。
输出必须是一个 JSON 对象：
{
  "type": "none | table | bar | line | pie",
  "title": "图表标题",
  "x_field": "分类字段或 null",
  "y_fields": ["指标字段"]
}
""".strip()


def _build_context(data: AgentFinalizationInput) -> dict[str, Any]:
    """把查询结果投影为两个模型都能直接消费的稳定上下文。"""

    return {
        "question": data.question,
        "intent": data.intent,
        "fields": data.execution.get("fields") or [],
        "row_count": data.execution.get("row_count", len(data.rows)),
        "rows": data.rows,
    }


def build_partial_finalization(
    *,
    execution: dict[str, Any],
    rows: list[dict[str, Any]],
    failed_stage: str | None = None,
    reason: str | None = None,
) -> AgentFinalizationResult:
    """图表或分析模型失败时的确定性部分作答：表格直出、注明失败环节。

    只使用执行结果中的真实数据（字段、行数、前几行），不引入任何模型输出，
    因此无论哪个生成环节失败都能安全收口。
    """

    fields = [str(field) for field in execution.get("fields") or []]
    row_count = execution.get("row_count")
    if not isinstance(row_count, int):
        row_count = len(rows)
    if reason is None:
        stage_note = f"（失败环节：{failed_stage}）" if failed_stage else ""
        reason = f"查询已成功执行，但分析回复生成失败{stage_note}，以下直接展示查询结果。"
    lines = [reason, f"- 行数：{row_count}"]
    if fields:
        lines.append(f"- 字段：{'、'.join(fields)}")
    preview_rows = [row for row in rows if isinstance(row, dict)][:3]
    if preview_rows:
        lines.append("- 结果预览：")
        for row in preview_rows:
            compact = "，".join(
                f"{key}={_preview_value(value)}" for key, value in list(row.items())[:6]
            )
            lines.append(f"  - {compact}")
    return AgentFinalizationResult(answer="\n".join(lines), chart={})


def _preview_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    return text if len(text) <= 40 else f"{text[:37]}..."


__all__ = [
    "AgentFinalizationInput",
    "AgentFinalizationResult",
    "AgentFinalizationService",
    "build_partial_finalization",
]
