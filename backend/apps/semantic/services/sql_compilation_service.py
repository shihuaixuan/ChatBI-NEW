from typing import Protocol

from apps.semantic.models.dto import (
    DatasetSchema,
    SemanticQueryCompileRequest,
    SemanticQueryCompileResult,
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
        )


__all__ = ["SemanticSQLCompilationService", "SemanticSQLCompilerPort"]
