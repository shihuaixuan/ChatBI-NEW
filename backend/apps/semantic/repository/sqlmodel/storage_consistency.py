from __future__ import annotations

from typing import Any

from apps.semantic.models.orm import (
    SemanticDimension,
    SemanticDimensionValue,
    SemanticModel,
    SemanticModelField,
    SemanticModelMeasure,
)


def check_storage_consistency(
    model: SemanticModel | None = None,
    fields: list[SemanticModelField] | None = None,
    measures: list[SemanticModelMeasure] | None = None,
    dimension: SemanticDimension | None = None,
    dimension_values: list[SemanticDimensionValue] | None = None,
) -> list[dict[str, Any]]:
    """核对迁移期 JSON 明细与结构化存储是否一致。"""
    issues: list[dict[str, Any]] = []
    if model is not None:
        json_fields = {
            _field_biz_name(item)
            for item in (model.model_detail or {}).get("fields", [])
            if isinstance(item, dict)
        }
        storage_fields = {item.biz_name for item in fields or [] if item.status == 1}
        if json_fields != storage_fields:
            issues.append(
                {
                    "type": "MODEL_FIELD_MISMATCH",
                    "json": sorted(json_fields),
                    "storage": sorted(storage_fields),
                }
            )

        json_measures = {
            _biz_name(item)
            for item in (model.model_detail or {}).get("measures", [])
            if isinstance(item, dict)
        }
        storage_measures = {
            item.biz_name for item in measures or [] if item.status == 1
        }
        if json_measures != storage_measures:
            issues.append(
                {
                    "type": "MODEL_MEASURE_MISMATCH",
                    "json": sorted(json_measures),
                    "storage": sorted(storage_measures),
                }
            )

    if dimension is not None:
        json_values = {
            _clean(item.get("value") or item.get("techName") or item.get("tech_name"))
            for item in dimension.dim_value_maps or []
            if isinstance(item, dict)
        }
        storage_values = {
            item.value
            for item in dimension_values or []
            if item.status == 1 and item.enabled
        }
        if json_values != storage_values:
            issues.append(
                {
                    "type": "DIMENSION_VALUE_MISMATCH",
                    "json": sorted(json_values),
                    "storage": sorted(storage_values),
                }
            )

    return issues


def _field_biz_name(item: dict[str, Any]) -> str:
    return _clean(
        item.get("bizName")
        or item.get("biz_name")
        or item.get("fieldName")
        or item.get("field_name")
    )


def _biz_name(item: dict[str, Any]) -> str:
    return _clean(item.get("bizName") or item.get("biz_name") or item.get("name"))


def _clean(value: Any) -> str:
    return str(value or "").strip()
