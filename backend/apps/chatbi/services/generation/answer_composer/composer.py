"""P1-7 AnswerComposer：摘要输入、claim 绑定、口径卡片和图表 spec。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.chatbi.errors import QuestionModelError
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.services.generation.answer_composer.caliber_card import (
    CaliberCard,
    build_caliber_card,
)
from apps.chatbi.services.generation.answer_composer.chart_spec import (
    ChartSpec,
    derive_chart_spec,
)
from apps.chatbi.services.generation.answer_composer.claims import (
    Claim,
    ClaimBindingError,
    validate_answer_numeric_coverage,
    validate_claim_bindings,
)
from apps.chatbi.services.generation.answer_composer.prompts import (
    ANSWER_COMPOSER_SYSTEM_PROMPT,
    build_composer_prompt,
)
from apps.chatbi.services.understanding.model_invocation import StructuredModelService


@dataclass(frozen=True, slots=True)
class AnswerComposerInput:
    """回答组装输入；rows 是服务端真实结果，不能由模型覆盖。"""

    question: str
    intent: dict[str, Any]
    execution: dict[str, Any]
    rows: list[dict[str, Any]]
    plan: dict[str, Any] = field(default_factory=dict)
    semantic_context: dict[str, Any] = field(default_factory=dict)
    mode: str = "fast"
    partial: bool = False


@dataclass(frozen=True, slots=True)
class AnswerComposerResult:
    """前端与生命周期收口使用的结构化回答。"""

    answer: str
    chart: dict[str, Any]
    claims: list[dict[str, Any]] = field(default_factory=list)
    caliber_card: dict[str, Any] = field(default_factory=dict)
    chart_spec: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False
    retry_count: int = 0


class _ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)


class AnswerComposer:
    """新编排模式使用的回答服务。

    图表由规则推导，模型只生成文字和结构化 claims；引用校验失败最多重试一次，
    仍失败时返回表格直出，确保不会把未绑定数字展示给用户。
    """

    def __init__(
        self,
        model_service: StructuredModelService | None = None,
        *,
        citation_enforced: bool = True,
        max_claim_retries: int = 1,
    ) -> None:
        self._model_service = model_service
        self._citation_enforced = citation_enforced
        self._max_claim_retries = max(0, min(max_claim_retries, 1))

    def compose(self, data: AnswerComposerInput) -> AnswerComposerResult:
        self._validate_input(data)
        card = build_caliber_card(data)
        chart_spec = derive_chart_spec(data)
        context = _build_context(data, card)
        if self._model_service is None:
            return self._table_fallback(data, card, chart_spec, "ANSWER_COMPOSER_MODEL_NOT_CONFIGURED")

        retry_count = 0
        retry_reason: str | None = None
        while True:
            try:
                output = self._invoke(data, context, retry_reason=retry_reason)
                claims = validate_claim_bindings(
                    output.claims,
                    execution=data.execution,
                    rows=data.rows,
                )
                if self._citation_enforced:
                    validate_answer_numeric_coverage(output.answer, claims)
                return AnswerComposerResult(
                    answer=output.answer.strip(),
                    chart=chart_spec.model_dump(mode="json", exclude_none=True),
                    claims=claims,
                    caliber_card=card.model_dump(mode="json", exclude_none=True),
                    chart_spec=chart_spec.model_dump(mode="json", exclude_none=True),
                    retry_count=retry_count,
                )
            except (ClaimBindingError, ValidationError, QuestionModelError) as exc:
                if retry_count < self._max_claim_retries:
                    retry_count += 1
                    retry_reason = str(exc)
                    continue
                return self._table_fallback(
                    data,
                    card,
                    chart_spec,
                    getattr(exc, "code", "ANSWER_COMPOSER_CLAIM_INVALID"),
                    retry_count=retry_count,
                )

    # generate 是服务层常见调用名，保留为 compose 的明确别名。
    generate = compose

    def _invoke(
        self,
        data: AnswerComposerInput,
        context: dict[str, Any],
        *,
        retry_reason: str | None,
    ) -> _ModelOutput:
        result = self._model_service.invoke(
            QuestionModelInvocationData(
                stage="answer_composer",
                system_prompt=ANSWER_COMPOSER_SYSTEM_PROMPT,
                user_prompt=build_composer_prompt(context, retry_reason=retry_reason),
                json_mode=QuestionModelJSONMode.STRICT,
            )
        )
        return _ModelOutput.model_validate(result.payload)

    @staticmethod
    def _validate_input(data: AnswerComposerInput) -> None:
        if not data.question.strip():
            raise ValueError("ANSWER_COMPOSER_QUESTION_REQUIRED")
        if data.execution.get("status") not in {None, "succeeded"}:
            raise ValueError("ANSWER_COMPOSER_EXECUTION_FAILED")
        if any(not isinstance(row, dict) for row in data.rows):
            raise ValueError("ANSWER_COMPOSER_ROWS_INVALID")

    def _table_fallback(
        self,
        data: AnswerComposerInput,
        card: CaliberCard,
        chart_spec: ChartSpec,
        warning: str,
        *,
        retry_count: int = 0,
    ) -> AnswerComposerResult:
        fields = [str(field) for field in data.execution.get("fields") or []]
        if not fields and data.rows:
            fields = list(dict.fromkeys(str(key) for row in data.rows for key in row))
        table = chart_spec.model_copy(
            update={
                "type": "table",
                "x": None,
                "y": [],
                "series": None,
                "columns": [{"name": field, "value": field} for field in fields],
                "data": data.rows[:100],
                "options": {},
            }
        )
        return AnswerComposerResult(
            answer="查询已完成，数字引用未通过校验，以下直接展示真实结果表。",
            chart=table.model_dump(mode="json", exclude_none=True),
            claims=[],
            caliber_card=card.model_dump(mode="json", exclude_none=True),
            chart_spec=table.model_dump(mode="json", exclude_none=True),
            warnings=[warning],
            degraded=True,
            retry_count=retry_count,
        )


def _build_context(data: AnswerComposerInput, card: CaliberCard) -> dict[str, Any]:
    execution = data.execution
    rows = [row for row in data.rows if isinstance(row, dict)]
    context = {
        "question": data.question,
        "mode": data.mode,
        "partial": data.partial,
        "intent": data.intent,
        "plan": data.plan,
        "fields": execution.get("fields") or list(dict.fromkeys(str(key) for row in rows for key in row)),
        "row_count": execution.get("row_count", len(rows)),
        "result_set_id": execution.get("result_set_id") or "query-0",
        "numeric_stats": execution.get("numeric_stats") or execution.get("stats_summary") or {},
        # 每行保留原始下标，模型只引用服务端真实行号。
        "key_rows": [
            {"row_index": index, "values": row}
            for index, row in _key_rows(rows)
        ],
        "caliber_card": card.model_dump(mode="json", exclude_none=True),
    }
    result_contract = execution.get("result_contract")
    if isinstance(result_contract, dict):
        context["result_contract"] = result_contract
    result_sets = _result_set_context(execution)
    if result_sets:
        context["result_sets"] = result_sets
    return context


def _result_set_context(execution: dict[str, Any]) -> list[dict[str, Any]]:
    """把 PLAN 的全部结果集压缩为可引用摘要，避免回答阶段只消费主结果。"""

    raw_sets = execution.get("result_sets")
    if not isinstance(raw_sets, dict):
        return []
    result = []
    for result_set_id, payload in raw_sets.items():
        if not isinstance(payload, dict):
            continue
        rows = [
            row
            for row in payload.get("rows") or payload.get("sample_rows") or []
            if isinstance(row, dict)
        ]
        result.append(
            {
                "result_set_id": str(result_set_id),
                "fields": payload.get("fields")
                or list(dict.fromkeys(str(key) for row in rows for key in row)),
                "row_count": payload.get("row_count", len(rows)),
                "key_rows": [
                    {"row_index": index, "values": row}
                    for index, row in _key_rows(rows)
                ],
            }
        )
    return result


def _key_rows(rows: list[dict[str, Any]], limit: int = 20) -> list[tuple[int, dict[str, Any]]]:
    if len(rows) <= limit:
        return list(enumerate(rows))
    indices = {0, len(rows) - 1}
    numeric_fields = list(rows[0]) if rows else []
    for column in numeric_fields:
        numeric = [(index, _number(row.get(column))) for index, row in enumerate(rows)]
        numeric = [(index, value) for index, value in numeric if value is not None]
        if numeric:
            indices.add(max(numeric, key=lambda item: item[1])[0])
            indices.add(min(numeric, key=lambda item: item[1])[0])
    selected = sorted(indices)
    if len(selected) < limit:
        selected.extend(index for index in range(len(rows)) if index not in indices)
    return [(index, rows[index]) for index in selected[:limit]]


def _contains_number(text: str) -> bool:
    return any(char.isdigit() for char in text)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return None


__all__ = ["AnswerComposer", "AnswerComposerInput", "AnswerComposerResult"]
