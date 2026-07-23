from typing import Any

from apps.chatbi.errors import SemanticQueryCompileError
from apps.chatbi.models import (
    SemanticQueryCompileData,
    SemanticQueryCompileResult,
    SemanticQueryUsedAsset,
)
from apps.semantic.models.dto import (
    SchemaElement,
    SemanticQueryCompileRequest,
)
from apps.semantic.models.dto import (
    SemanticQueryCompileResult as SemanticCompileResult,
)
from apps.semantic.services import SemanticSQLCompilationService


class SemanticCompilationService:
    """Agent 与 Graph 共用的语义 SQL 编译入口（直连 Semantic 公开编译服务）。"""

    def __init__(self, gateway: SemanticSQLCompilationService) -> None:
        self._gateway = gateway

    def compile(
        self,
        data: SemanticQueryCompileData,
    ) -> SemanticQueryCompileResult:
        try:
            compiled = self._gateway.compile(
                SemanticQueryCompileRequest(
                    workspace_id=data.workspace_id,
                    dataset_id=data.dataset_id,
                    question=data.question,
                    slots=data.slots,
                    repair_context=data.repair_context,
                    order_by=data.order_by,
                    limit=data.limit,
                    time_bucket=data.time_bucket,
                    select_mode=data.select_mode,
                    having=data.having,
                )
            )
        except ValueError as exc:
            raise SemanticQueryCompileError(str(exc)) from exc

        return SemanticQueryCompileResult(
            dataset_id=compiled.dataset_id,
            sql=compiled.sql,
            tables=compiled.tables,
            metrics=compiled.metrics,
            dimensions=compiled.dimensions,
            datasource_id=self._datasource_id(compiled),
            used_assets=[
                *self._used_assets(
                    "METRIC",
                    compiled.metrics,
                    compiled.schema.metrics,
                ),
                *self._used_assets(
                    "DIMENSION",
                    compiled.dimensions,
                    compiled.schema.dimensions,
                ),
            ],
        )

    @staticmethod
    def _used_assets(
        asset_type: str,
        biz_names: list[str],
        elements: list[SchemaElement],
    ) -> list[SemanticQueryUsedAsset]:
        element_by_name = {element.biz_name: element for element in elements}
        assets: list[SemanticQueryUsedAsset] = []
        for biz_name in biz_names:
            element = element_by_name.get(biz_name)
            if element is None:
                continue
            assets.append(
                SemanticQueryUsedAsset(
                    asset_type=asset_type,
                    asset_id=element.id,
                    biz_name=element.biz_name,
                )
            )
        return assets

    @classmethod
    def _datasource_id(cls, compiled: SemanticCompileResult) -> int | None:
        selected = {*compiled.metrics, *compiled.dimensions}
        model_by_id = {model.get("id"): model for model in compiled.schema.models}
        for element in [*compiled.schema.metrics, *compiled.schema.dimensions]:
            if element.biz_name not in selected:
                continue
            model = model_by_id.get(element.model) or {}
            datasource_id = cls._int_or_none(
                model.get("datasource_id") or model.get("datasourceId")
            )
            if datasource_id is not None:
                return datasource_id
            datasource_id = cls._int_or_none(cls._measure_datasource_id(element))
            if datasource_id is not None:
                return datasource_id
        return None

    @staticmethod
    def _measure_datasource_id(element: SchemaElement) -> Any:
        params = element.type_params or {}
        measure_params = (
            params.get("metricDefineByMeasureParams")
            if isinstance(params, dict)
            else {}
        )
        measures = (
            measure_params.get("measures")
            if isinstance(measure_params, dict)
            else []
        )
        if measures and isinstance(measures[0], dict):
            return measures[0].get("datasourceId") or measures[0].get(
                "datasource_id"
            )
        return None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None


__all__ = [
    "SemanticCompilationService",
    "SemanticQueryCompileError",
]
