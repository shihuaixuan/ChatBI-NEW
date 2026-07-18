from __future__ import annotations

from typing import Any

from apps.semantic.models.dto import (
    ModelBuildField,
    ModelBuildSchemaResult,
    ModelCreateWithAssetsPayload,
    SemanticColumnMeta,
)
from apps.semantic.models.orm import SemanticDimension, SemanticModel
from apps.semantic.repository.model_repository import SemanticModelAssetBundle
from apps.semantic.utils.orm_mapping import normalize_model_source


class SemanticModelBuilder:
    """根据数据源字段生成语义模型结构。"""

    def build_table_schema(
        self,
        table_name: str | None,
        columns: list[SemanticColumnMeta],
        source_type: str = "TABLE",
        sql: str | None = None,
        datasource_id: int | None = None,
    ) -> ModelBuildSchemaResult:
        fields = [self._build_field(column) for column in columns if column.checked]
        model_detail = {
            "queryType": (
                "sql_query" if source_type.upper() == "SQL" else "table_query"
            ),
            "tableQuery": {"table": table_name} if table_name else {},
            "sqlQuery": {"sql": sql} if sql else {},
            "fields": [self._field_detail(field) for field in fields],
            "identifiers": [
                self._identifier_detail(field)
                for field in fields
                if field.role == "IDENTIFIER"
            ],
            "dimensions": [
                self._dimension_detail(field)
                for field in fields
                if field.role in {"IDENTIFIER", "DIMENSION"}
            ],
            "measures": [
                self._measure_detail(field, datasource_id=datasource_id)
                for field in fields
                if field.role == "MEASURE"
            ],
            "sqlVariables": [],
        }
        return ModelBuildSchemaResult(
            source_type=source_type,
            table_name=table_name,
            sql=sql,
            fields=fields,
            model_detail=model_detail,
        )

    def _build_field(self, column: SemanticColumnMeta) -> ModelBuildField:
        field_name = column.field_name
        data_type = column.field_type or ""
        role = _infer_field_role(field_name, data_type)
        return ModelBuildField(
            field_name=field_name,
            data_type=data_type,
            name=_display_name(column.field_comment, field_name),
            biz_name=field_name,
            expr=field_name,
            role=role,
            default_agg="SUM" if role == "MEASURE" else None,
            semantic_type="time" if _is_time_type(data_type) else None,
            create_asset=role in {"IDENTIFIER", "DIMENSION", "MEASURE"},
        )

    def _field_detail(self, field: ModelBuildField) -> dict[str, Any]:
        return {
            "fieldName": field.field_name,
            "dataType": field.data_type,
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
        }

    def _identifier_detail(self, field: ModelBuildField) -> dict[str, Any]:
        return {
            "name": field.name,
            "bizName": field.biz_name,
            "fieldName": field.field_name,
            "type": "primary",
        }

    def _dimension_detail(self, field: ModelBuildField) -> dict[str, Any]:
        dimension_type = _dimension_type_from_field(field)
        detail = {
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
            "dataType": field.data_type,
            "type": dimension_type,
            "semanticType": field.semantic_type,
            "alias": field.alias,
            "createDimension": field.create_asset,
        }
        if is_time_dimension_type(dimension_type):
            detail["dateFormat"] = "yyyy-MM-dd"
            detail["typeParams"] = {
                "isPrimary": "true",
                "timeGranularity": "day",
            }
        return detail

    def _measure_detail(
        self, field: ModelBuildField, datasource_id: int | None = None
    ) -> dict[str, Any]:
        detail = {
            "name": field.name,
            "bizName": field.biz_name,
            "expr": field.expr,
            "agg": field.default_agg or "SUM",
            "alias": field.alias,
            "isCreateMetric": 0,
            "createMetric": False,
        }
        if datasource_id is not None:
            detail["datasourceId"] = datasource_id
        return detail


def build_model_with_assets(
    payload: ModelCreateWithAssetsPayload, oid: int = 0
) -> SemanticModelAssetBundle:
    model_detail = _normalize_model_detail(payload)
    model = SemanticModel(
        oid=oid,
        domain_id=payload.domain_id,
        datasource_id=payload.datasource_id,
        name=payload.name,
        biz_name=payload.biz_name,
        description=payload.description,
        model_detail=model_detail,
        filter_sql=payload.filter_sql,
        alias=payload.alias,
        source_type=payload.source_type,
        depends=payload.depends,
        table_name=payload.table_name,
        sql_query=payload.sql,
    )
    normalize_model_source(model)
    dimensions = [
        _dimension_from_detail(item, oid=oid, model_id=0)
        for item in model_detail.get("dimensions", [])
        if item.get("createDimension", True)
    ]
    return SemanticModelAssetBundle(model=model, dimensions=dimensions, metrics=[])


def is_time_dimension_type(dimension_type: str | None) -> bool:
    return dimension_type in {"time", "partition_time"}


def _dimension_type_from_field(field: ModelBuildField) -> str:
    if field.role == "IDENTIFIER":
        return "primary_key"
    if field.semantic_type == "time":
        return "partition_time"
    return "categorical"


def _normalize_model_detail(payload: ModelCreateWithAssetsPayload) -> dict[str, Any]:
    model_detail = dict(payload.model_detail or {})
    if payload.source_type.upper() == "SQL":
        model_detail.setdefault("queryType", "sql_query")
    else:
        model_detail.setdefault("queryType", "table_query")
    if payload.table_name:
        model_detail.setdefault("tableQuery", {"table": payload.table_name})
    if payload.sql:
        model_detail.setdefault("sqlQuery", {"sql": payload.sql})
    model_detail.setdefault("fields", [])
    model_detail.setdefault("identifiers", [])
    model_detail.setdefault("dimensions", [])
    model_detail.setdefault("measures", [])
    model_detail.setdefault("sqlVariables", [])
    return model_detail


def _dimension_from_detail(
    item: dict[str, Any], oid: int, model_id: int
) -> SemanticDimension:
    dimension_type = item.get("type") or "categorical"
    return SemanticDimension(
        oid=oid,
        model_id=model_id,
        name=item.get("name") or item.get("bizName") or item.get("biz_name"),
        biz_name=item.get("bizName") or item.get("biz_name"),
        alias=item.get("alias") or [],
        type=dimension_type,
        semantic_type=item.get("semanticType") or item.get("semantic_type"),
        expr=item.get("expr") or item.get("bizName") or item.get("biz_name"),
        data_type=item.get("dataType") or item.get("data_type"),
        field_name=(
            item.get("fieldName")
            or item.get("field_name")
            or item.get("bizName")
            or item.get("biz_name")
        ),
        is_primary_key=dimension_type == "primary_key",
        is_default_time=is_time_dimension_type(dimension_type),
        time_granularities=[(item.get("typeParams") or {}).get("timeGranularity")]
        if isinstance(item.get("typeParams"), dict)
        and (item.get("typeParams") or {}).get("timeGranularity")
        else [],
        description=item.get("description"),
        type_params=item.get("typeParams") or item.get("type_params") or {},
    )


def _infer_field_role(field_name: str, data_type: str | None) -> str:
    normalized = field_name.lower()
    if normalized == "id" or normalized.endswith("_id"):
        return "IDENTIFIER"
    if _is_time_type(data_type) or any(
        token in normalized for token in ["date", "time", "day", "month"]
    ):
        return "DIMENSION"
    if _is_numeric_type(data_type):
        return "MEASURE"
    return "DIMENSION"


def _display_name(comment: str | None, field_name: str) -> str:
    text = str(comment or "").strip()
    return text or field_name


def _is_numeric_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(
        token in lowered
        for token in ["int", "decimal", "double", "float", "numeric", "number", "real"]
    )


def _is_time_type(data_type: str | None) -> bool:
    lowered = str(data_type or "").lower()
    return any(
        token in lowered for token in ["date", "time", "timestamp", "datetime"]
    )
