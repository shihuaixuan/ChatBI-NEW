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
    answer = data.answer_markdown
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
    )


__all__ = [
    "FinalReplyProjectionError",
    "project_final_reply",
    "project_query_final_reply",
]
