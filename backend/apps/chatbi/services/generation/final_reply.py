"""最终回答、推荐问题和图表的组合规则（纯函数）。"""

from typing import Any

from apps.chatbi.errors import FinalReplyProjectionError
from apps.chatbi.models.dto.final_reply import (
    FinalReplyProjectionData,
    FinalReplyProjectionResult,
    QueryFinalReplyProjectionData,
    QueryFinalReplyProjectionResult,
)

_DEFAULT_FINAL_ANSWER = "暂时无法生成完整回答，请稍后重试。"
_NON_STANDARD_SQL_NOTE = (
    "\n\n> 注：本次 SQL 由 AI 直接生成（非标准指标口径），结果口径可能与指标定义存在差异。"
)


def project_final_reply(data: FinalReplyProjectionData) -> FinalReplyProjectionResult:
    """生成前端消费的稳定最终回复。"""

    return FinalReplyProjectionResult(
        final_answer=str(data.answer.get("answer") or _DEFAULT_FINAL_ANSWER),
        recommendations=list(data.recommendations.get("questions") or []),
        chart=dict(data.chart),
        claims=list(data.claims or data.answer.get("claims") or []),
        caliber_card=dict(data.caliber_card or data.answer.get("caliber_card") or {}),
        chart_spec=dict(data.chart_spec or data.answer.get("chart_spec") or {}),
        metadata={"source": "real_chatbi_v1"},
    )


def project_query_final_reply(
    data: QueryFinalReplyProjectionData,
) -> QueryFinalReplyProjectionResult:
    """根据成功执行结果生成查询最终回答和图表建议。"""

    execution = data.execution
    if not execution:
        raise FinalReplyProjectionError(
            "execution_required_before_finish",
            "尚无成功的 execute_sql 结果，禁止凭空作答。请先执行查询，或如实说明无法完成。",
        )

    non_standard = execution.get("sql_source") == "manual"
    # 只要执行层提供了完整结果，最终文本就由结果确定性投影；
    # 模型 Markdown 仅保留给没有结果行快照的兼容调用。
    answer = (
        _render_grounded_answer(data.rows, execution, data.intent)
        if data.rows is not None
        else data.answer_markdown
    )
    if non_standard:
        answer += _NON_STANDARD_SQL_NOTE

    chart: dict[str, Any] = {}
    if data.chart_type and data.chart_type != "table":
        fields = execution.get("fields") or []
        chart = {
            "type": data.chart_type,
            "x": data.x_field or (fields[0] if fields else None),
            "y": data.y_fields or list(fields[1:2]),
        }

    return QueryFinalReplyProjectionResult(
        answer=answer,
        chart=chart,
        sql=execution.get("sql"),
        non_standard=non_standard,
        claims=list(data.claims),
        caliber_card=dict(data.caliber_card),
        chart_spec=dict(data.chart_spec),
    )


def _render_grounded_answer(
    rows: list[dict[str, Any]],
    execution: dict[str, Any],
    intent: dict[str, Any],
) -> str:
    """从真实结果行生成回答，禁止模型重新抄写或改写查询数值。"""

    normalized_rows = [dict(row) for row in rows if isinstance(row, dict)]
    if not normalized_rows:
        return "查询执行成功，但没有返回符合条件的数据。"

    fields = [
        str(field)
        for field in execution.get("fields") or []
        if str(field or "").strip()
    ]
    if not fields:
        fields = list(
            dict.fromkeys(
                str(key)
                for row in normalized_rows
                for key in row
            )
        )

    if str(intent.get("intent_type") or "") == "share_analysis":
        normalized_rows, fields = _append_share_column(normalized_rows, fields)

    if len(normalized_rows) == 1 and len(fields) == 1:
        field = fields[0]
        return f"查询结果：**{_escape_markdown(field)} = {_format_cell(normalized_rows[0].get(field))}**。"

    header = "| " + " | ".join(_escape_markdown(field) for field in fields) + " |"
    separator = "| " + " | ".join("---" for _ in fields) + " |"
    body = [
        "| "
        + " | ".join(_format_cell(row.get(field)) for field in fields)
        + " |"
        for row in normalized_rows
    ]
    return "\n".join(["查询结果如下：", "", header, separator, *body])


def _append_share_column(
    rows: list[dict[str, Any]],
    fields: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """占比查询以首个数值指标为分子，并用当前分组结果合计作为分母。"""

    metric_field = next(
        (
            field
            for field in reversed(fields)
            if any(_numeric(row.get(field)) is not None for row in rows)
        ),
        None,
    )
    if metric_field is None:
        return rows, fields
    values = [_numeric(row.get(metric_field)) for row in rows]
    total = sum(value for value in values if value is not None)
    if total == 0:
        return rows, fields
    projected = []
    for row, value in zip(rows, values, strict=True):
        item = dict(row)
        item["占比"] = None if value is None else f"{value / total:.2%}"
        projected.append(item)
    return projected, [*fields, "占比"]


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _format_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    return _escape_markdown(str(value))


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


__all__ = [
    "FinalReplyProjectionError",
    "project_final_reply",
    "project_query_final_reply",
]
