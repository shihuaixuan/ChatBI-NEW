from typing import Any, Protocol

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto import (
    DatasetSchema,
    SchemaElement,
    SemanticPlanStatus,
    SemanticQueryCompileRequest,
    SemanticQueryCompileResult,
    SemanticQueryPlan,
    SemanticUsedAsset,
)
from apps.semantic.services.query.validation import SemanticQueryValidationService
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

    def compile_verified_plan(
        self,
        schema: DatasetSchema,
        plan: SemanticQueryPlan,
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
        self._plan_validator = SemanticQueryValidationService()

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
            datasource_id=_datasource_id(
                result.metric_ids,
                result.dimension_ids,
                schema,
            ),
            used_assets=[
                *_used_assets(
                    "METRIC",
                    result.metric_ids,
                    schema.metrics,
                ),
                *_used_assets(
                    "DIMENSION",
                    result.dimension_ids,
                    schema.dimensions,
                ),
            ],
        )

    def compile_verified_plan(
        self,
        workspace_id: int,
        plan: SemanticQueryPlan,
    ) -> SemanticQueryCompileResult:
        """验证计划与最新 Schema 后，执行不可变资产编译。"""

        schema = self._schema_provider.build_dataset_schema(
            workspace_id,
            plan.dataset_id,
        )
        report = self._plan_validator.validate(plan, schema)
        if report.status != SemanticPlanStatus.PROVEN:
            reason = report.reason_codes[0] if report.reason_codes else "SEMANTIC_QUERY_PLAN_INVALID"
            raise SemanticValidationError(reason)
        try:
            result = self._compiler.compile_verified_plan(schema, plan)
        except ValueError as error:
            raise SemanticValidationError(str(error)) from error
        return SemanticQueryCompileResult(
            dataset_id=plan.dataset_id,
            sql=result.sql,
            tables=result.tables,
            metrics=result.metrics,
            dimensions=result.dimensions,
            schema=schema,
            datasource_id=_datasource_id(
                result.metric_ids,
                result.dimension_ids,
                schema,
            ),
            used_assets=[
                *_used_assets("METRIC", result.metric_ids, schema.metrics),
                *_used_assets("DIMENSION", result.dimension_ids, schema.dimensions),
            ],
        )


def _used_assets(
    asset_type: str,
    asset_ids: list[int],
    elements: list[SchemaElement],
) -> list[SemanticUsedAsset]:
    element_by_id = {element.id: element for element in elements}
    return [
        SemanticUsedAsset(
            asset_type=asset_type,
            asset_id=asset_id,
            biz_name=element_by_id[asset_id].biz_name,
        )
        for asset_id in asset_ids
        if asset_id in element_by_id
    ]


def _datasource_id(
    metric_ids: list[int],
    dimension_ids: list[int],
    schema: DatasetSchema,
) -> int | None:
    selected = {*metric_ids, *dimension_ids}
    model_by_id = {model.get("id"): model for model in schema.models}
    for element in [*schema.metrics, *schema.dimensions]:
        if element.id not in selected:
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
