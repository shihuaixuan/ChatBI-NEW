"""回答 claim 的结构化契约和结果集绑定校验。"""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    """模型生成的一个可验证结论。

    含数字的结论必须同时提供 result_set_id、row_index 和 field，
    服务端会使用原始结果集重新读取该位置，不信任模型提供的 value。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    value: Any | None = None
    result_set_id: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    field: str | None = None
    unit: str | None = None


class ClaimBindingError(ValueError):
    """claim 无法绑定到真实结果集。"""

    def __init__(self, code: str, message: str, *, claim_index: int | None = None) -> None:
        self.code = code
        self.claim_index = claim_index
        super().__init__(message)


_NUMBER_PATTERN = re.compile(r"(?<![\w.])-?\d+(?:[,.]\d+)*(?:\.\d+)?%?(?![\w.])")


def validate_claim_bindings(
    claims: list[Claim],
    *,
    execution: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """校验并返回带服务端真实值的 claim。

    返回值中的 value 始终来自结果行，而不是模型请求中的 value；
    这样即使模型在文本中写错数字，也不会被回答契约接受。
    """

    result_sets = _result_sets(execution, rows)
    normalized: list[dict[str, Any]] = []
    for index, claim in enumerate(claims):
        if _contains_number(claim.text):
            if (
                not claim.result_set_id
                or claim.row_index is None
                or not claim.field
            ):
                raise ClaimBindingError(
                    "CLAIM_NUMERIC_REFERENCE_REQUIRED",
                    "含数字的 claim 必须提供结果集、行号和字段引用。",
                    claim_index=index,
                )
            result_rows = result_sets.get(claim.result_set_id)
            if result_rows is None:
                raise ClaimBindingError(
                    "CLAIM_RESULT_SET_NOT_FOUND",
                    f"claim 引用的结果集不存在：{claim.result_set_id}。",
                    claim_index=index,
                )
            if claim.row_index >= len(result_rows):
                raise ClaimBindingError(
                    "CLAIM_ROW_NOT_FOUND",
                    f"claim 引用的结果行不存在：{claim.row_index}。",
                    claim_index=index,
                )
            row = result_rows[claim.row_index]
            if claim.field not in row:
                raise ClaimBindingError(
                    "CLAIM_FIELD_NOT_FOUND",
                    f"claim 引用的结果字段不存在：{claim.field}。",
                    claim_index=index,
                )
            actual = row[claim.field]
            if claim.value is not None and not _same_value(claim.value, actual):
                raise ClaimBindingError(
                    "CLAIM_VALUE_MISMATCH",
                    f"claim 数值与结果集字段不一致：{claim.field}。",
                    claim_index=index,
                )
            _validate_claim_text_numbers(
                claim.text,
                row,
                claim_index=index,
            )
            normalized.append(
                claim.model_copy(
                    update={"value": actual},
                ).model_dump(mode="json", exclude_none=True)
            )
            continue

        # 无数字的解释性 claim 不强制虚构定位，但仍保留结构化形态。
        normalized.append(claim.model_dump(mode="json", exclude_none=True))
    return normalized


def validate_answer_numeric_coverage(
    answer: str,
    claims: list[dict[str, Any]],
) -> None:
    """确保回答中的每个数字都出现在已完成结果集绑定的 claim 中。"""

    answer_numbers = _number_tokens(answer)
    if not answer_numbers:
        return
    claim_numbers = [
        number
        for claim in claims
        for number in _number_tokens(str(claim.get("text") or ""))
    ]
    uncovered = [
        raw
        for raw, number in answer_numbers
        if not any(_same_number(number, candidate) for _, candidate in claim_numbers)
    ]
    if uncovered:
        raise ClaimBindingError(
            "ANSWER_NUMERIC_CLAIM_INCOMPLETE",
            "回答包含未绑定到结果集的数字：" + ", ".join(uncovered),
        )


def _validate_claim_text_numbers(
    text: str,
    row: dict[str, Any],
    *,
    claim_index: int,
) -> None:
    """claim 文本中的数字只能来自它引用的真实结果行。"""

    text_numbers = _number_tokens(text)
    if not text_numbers:
        return
    row_numbers = [
        number
        for value in row.values()
        for _, number in _number_tokens(str(value))
    ]
    unsupported = [
        raw
        for raw, number in text_numbers
        if not any(_same_number(number, candidate) for candidate in row_numbers)
    ]
    if unsupported:
        raise ClaimBindingError(
            "CLAIM_TEXT_VALUE_MISMATCH",
            "claim 文本包含结果行中不存在的数字：" + ", ".join(unsupported),
            claim_index=claim_index,
        )


def _result_sets(
    execution: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """兼容单结果和 PLAN 的多结果摘要，统一成结果集 ID 映射。"""

    result_sets: dict[str, list[dict[str, Any]]] = {}
    raw_sets = execution.get("result_sets")
    if isinstance(raw_sets, dict):
        items = raw_sets.items()
    elif isinstance(raw_sets, list):
        items = ((item.get("result_set_id"), item) for item in raw_sets if isinstance(item, dict))
    else:
        items = ()
    for result_set_id, payload in items:
        if not result_set_id or not isinstance(payload, dict):
            continue
        candidate_rows = payload.get("rows") or payload.get("sample_rows") or []
        if isinstance(candidate_rows, list):
            result_sets[str(result_set_id)] = [
                row for row in candidate_rows if isinstance(row, dict)
            ]

    default_id = str(execution.get("result_set_id") or "query-0")
    result_sets.setdefault(default_id, [row for row in rows if isinstance(row, dict)])
    # 模型有时使用 node_id 作为引用；若执行层只暴露一个结果集，允许该稳定别名。
    if len(result_sets) == 1:
        only_rows = next(iter(result_sets.values()))
        result_sets.setdefault("query-0", only_rows)
    return result_sets


def _contains_number(text: str) -> bool:
    return bool(_NUMBER_PATTERN.search(text))


def _number_tokens(text: str) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw = match.group(0)
        value = _number(raw)
        if value is not None:
            result.append((raw, value))
    return result


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def _same_value(left: Any, right: Any) -> bool:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None or right_number is not None:
        return (
            left_number is not None
            and right_number is not None
            and math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-9)
        )
    return str(left) == str(right)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(",", "").rstrip("%")
        try:
            number = float(normalized)
        except ValueError:
            return None
        return number / 100 if value.strip().endswith("%") else number
    return None


__all__ = [
    "Claim",
    "ClaimBindingError",
    "validate_answer_numeric_coverage",
    "validate_claim_bindings",
]
