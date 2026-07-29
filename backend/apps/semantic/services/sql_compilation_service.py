from typing import Any, Protocol

from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticQueryCompileRequest,
    SemanticQueryCompileResult,
    SemanticUsedAsset,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.semantic.services.sql_compiler import (
    SemanticSQLCompileRequest,
    SemanticSQLCompileResult,
)


class SemanticSQLCompilerPort(Protocol):
    """Semantic 编译算法的最小端口。"""

    def compile(
        self,
        request: SemanticSQLCompileRequest,
    ) -> SemanticSQLCompileResult: ...


class SemanticSQLCompilationService:
    """统一加载数据集 Schema 并执行语义 SQL 编译。"""

    def __init__(
        self,
        schema_provider: DatasetSchemaProvider,
        compiler: SemanticSQLCompilerPort,
    ) -> None:
        self._schema_provider = schema_provider
        self._compiler = compiler

    def compile(
        self,
        request: SemanticQueryCompileRequest,
    ) -> SemanticQueryCompileResult:
        schema: DatasetSchema = self._schema_provider.build_dataset_schema(
            request.workspace_id,
            request.dataset_id,
        )
        result = self._compiler.compile(
            SemanticSQLCompileRequest(
                schema=schema,
                question=request.question,
                slots=request.slots,
                repair_context=request.repair_context,
                order_by=request.order_by,
                limit=request.limit,
                time_bucket=request.time_bucket,
                select_mode=request.select_mode,
                having=request.having,
            )
        )
        return SemanticQueryCompileResult(
            dataset_id=request.dataset_id,
            sql=result.sql,
            tables=result.tables,
            metrics=result.metrics,
            dimensions=result.dimensions,
            schema=schema,
            datasource_id=_datasource_id(result.metrics, result.dimensions, schema),
            used_assets=[
                *_used_assets("METRIC", result.metrics, schema.metrics),
                *_used_assets("DIMENSION", result.dimensions, schema.dimensions),
            ],
        )


def _used_assets(
    asset_type: str,
    biz_names: list[str],
    elements: list[SchemaElement],
) -> list[SemanticUsedAsset]:
    element_by_name = {element.biz_name: element for element in elements}
    return [
        SemanticUsedAsset(
            asset_type=asset_type,
            asset_id=element_by_name[biz_name].id,
            biz_name=biz_name,
        )
        for biz_name in biz_names
        if biz_name in element_by_name
    ]


def _datasource_id(
    metrics: list[str],
    dimensions: list[str],
    schema: DatasetSchema,
) -> int | None:
    selected = {*metrics, *dimensions}
    model_by_id = {model.get("id"): model for model in schema.models}
    for element in [*schema.metrics, *schema.dimensions]:
        if element.biz_name not in selected:
            continue
        model = model_by_id.get(element.model) or {}
        datasource_id = _positive_int(
            model.get("datasource_id") or model.get("datasourceId")
        )
        if datasource_id is not None:
            return datasource_id
        params = element.type_params or {}
        measure_params = (
            params.get("metricDefineByMeasureParams")
            if isinstance(params, dict)
            else {}
        )
        measures = (
            measure_params.get("measures") if isinstance(measure_params, dict) else []
        )
        if measures and isinstance(measures[0], dict):
            datasource_id = _positive_int(
                measures[0].get("datasourceId") or measures[0].get("datasource_id")
            )
            if datasource_id is not None:
                return datasource_id
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


__all__ = ["SemanticSQLCompilationService", "SemanticSQLCompilerPort"]
