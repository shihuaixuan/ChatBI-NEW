"""把 Research 子计划结果投影为受控证据摘要。"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from apps.chatbi.errors import ResearchExecutionError
from apps.chatbi.models.dto.research import (
    EvidenceLineage,
    EvidenceRow,
    EvidenceRowValue,
    EvidenceSnapshot,
    EvidenceStatistics,
    ResearchRowOrder,
)
from apps.chatbi.services.research.actions import MaterializedResearchAction


def project_evidence_snapshot(
    *,
    materialized: MaterializedResearchAction,
    plan_id: str,
    task_id: str,
    result_id: str,
    rows: list[dict[str, Any]],
    fields: list[str],
    evidence_index: int,
    max_rows: int,
    max_chars: int,
) -> EvidenceSnapshot:
    """按固定排序采样，不把 SQL、物理字段或完整结果交给模型。"""

    if evidence_index <= 0 or max_rows <= 0 or max_chars <= 0:
        raise ResearchExecutionError(ResearchExecutionError.EVIDENCE_RESULT_INVALID)
    projected_fields = [item.field for item in materialized.columns]
    if not set(projected_fields) <= set(fields):
        raise ResearchExecutionError(
            ResearchExecutionError.EVIDENCE_RESULT_INVALID,
            details={"reason": "RESEARCH_EVIDENCE_FIELDS_MISSING"},
        )
    order_index = next(
        (
            index
            for index, item in enumerate(materialized.columns)
            if item.logical_column.value_role in {"value", "difference", "growth_rate"}
        ),
        None,
    )
    if order_index is None:
        raise ResearchExecutionError(
            ResearchExecutionError.EVIDENCE_RESULT_INVALID,
            details={"reason": "RESEARCH_EVIDENCE_ORDER_COLUMN_REQUIRED"},
        )
    order_projection = materialized.columns[order_index]
    metric_ref = order_projection.logical_column.metric_ref
    if metric_ref is None:
        raise ResearchExecutionError(ResearchExecutionError.EVIDENCE_RESULT_INVALID)
    sortable = sorted(
        (
            row
            for row in rows
            if _decimal_value(row.get(order_projection.field)) is not None
        ),
        key=lambda row: _numeric_sort_key(row.get(order_projection.field)),
        reverse=True,
    )
    top_limit = (max_rows + 1) // 2
    bottom_limit = max_rows // 2
    top_source = sortable[:top_limit]
    bottom_source = list(reversed(sortable[-bottom_limit:])) if bottom_limit else []
    top_rows = _project_rows(top_source, materialized)
    bottom_rows = _project_rows(bottom_source, materialized)
    top_rows, bottom_rows = _fit_character_budget(
        top_rows,
        bottom_rows,
        max_chars=max_chars,
    )
    numeric_values = [
        _decimal_value(row.get(order_projection.field))
        for row in rows
    ]
    comparable = [item for item in numeric_values if item is not None]
    return EvidenceSnapshot(
        evidence_id=f"evidence:{evidence_index}",
        result_id=result_id,
        purpose=materialized.purpose,
        metric_refs=materialized.metric_refs,
        dimension_refs=materialized.dimension_refs,
        time_roles=materialized.time_roles,
        logical_columns=tuple(item.logical_column for item in materialized.columns),
        row_order=ResearchRowOrder(
            metric_ref=metric_ref,
            value_role=cast(
                Literal["value", "current", "previous", "difference", "growth_rate"],
                order_projection.logical_column.value_role,
            ),
        ),
        statistics=EvidenceStatistics(
            row_count=len(rows),
            positive_count=sum(item > 0 for item in comparable),
            negative_count=sum(item < 0 for item in comparable),
            null_count=sum(item is None for item in numeric_values),
            truncated=(len(rows) > max_rows),
        ),
        top_rows=top_rows,
        bottom_rows=bottom_rows,
        lineage=EvidenceLineage(
            plan_id=plan_id,
            task_id=task_id,
            action_fingerprint=materialized.fingerprint,
        ),
    )


def premise_supported(goal: str, evidence: EvidenceSnapshot) -> bool | None:
    """仅对用户明确声明的上涨或下降方向进行确定性前提校验。"""

    if (
        len(evidence.metric_refs) != 1
        or evidence.row_order.value_role == "value"
        or not evidence.top_rows
    ):
        return None
    order_index = next(
        index
        for index, item in enumerate(evidence.logical_columns)
        if item.metric_ref == evidence.row_order.metric_ref
        and item.value_role == evidence.row_order.value_role
    )
    value = next(
        (
            item.value
            for item in evidence.top_rows[0].values
            if item.logical_column_index == order_index
        ),
        None,
    )
    number = _decimal_value(value)
    if number is None:
        return None
    decline_words = ("下降", "下滑", "降低", "减少", "跌")
    increase_words = ("上升", "上涨", "增长", "增加", "提升")
    expects_decline = any(word in goal for word in decline_words)
    expects_increase = any(word in goal for word in increase_words)
    if expects_decline and not expects_increase:
        return number < 0
    if expects_increase and not expects_decline:
        return number > 0
    return None


def _project_rows(
    rows: list[dict[str, Any]],
    materialized: MaterializedResearchAction,
) -> tuple[EvidenceRow, ...]:
    return tuple(
        EvidenceRow(
            values=tuple(
                EvidenceRowValue(logical_column_index=index, value=row.get(item.field))
                for index, item in enumerate(materialized.columns)
            )
        )
        for row in rows
    )


def _fit_character_budget(
    top_rows: tuple[EvidenceRow, ...],
    bottom_rows: tuple[EvidenceRow, ...],
    *,
    max_chars: int,
) -> tuple[tuple[EvidenceRow, ...], tuple[EvidenceRow, ...]]:
    top = list(top_rows)
    bottom = list(bottom_rows)
    while top or bottom:
        payload = {
            "top_rows": [item.model_dump(mode="json") for item in top],
            "bottom_rows": [item.model_dump(mode="json") for item in bottom],
        }
        if len(json.dumps(payload, ensure_ascii=False, default=str)) <= max_chars:
            return tuple(top), tuple(bottom)
        if len(bottom) >= len(top) and bottom:
            bottom.pop()
        elif top:
            top.pop()
    return (), ()


def _numeric_sort_key(value: Any) -> tuple[bool, Decimal]:
    number = _decimal_value(value)
    return (number is not None, number or Decimal(0))


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["premise_supported", "project_evidence_snapshot"]
