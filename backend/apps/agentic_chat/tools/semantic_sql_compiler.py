from sqlalchemy import select

from apps.agentic_chat.schemas import ToolResult
from apps.headless.models import HeadlessDataSet, HeadlessModel
from apps.headless.service import HeadlessSchemaBuilder
from apps.headless.sql_compiler import SemanticSQLCompiler, SemanticSQLCompileRequest


class SemanticSQLCompilerTool:
    name = "sql.generate_semantic_compiler"

    def __init__(self, session, compiler: SemanticSQLCompiler | None = None):
        self.session = session
        self.compiler = compiler or SemanticSQLCompiler()

    def can_compile(self, oid: int, datasource_id: int | None) -> bool:
        if not datasource_id:
            return False
        return self._resolve_dataset(oid=oid, datasource_id=datasource_id) is not None

    def run(self, payload: dict) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        oid = payload.get("oid") or 1
        if not datasource_id:
            return ToolResult(success=False, error_code="datasource_required", message="缺少数据源")
        dataset = self._resolve_dataset(oid=oid, datasource_id=datasource_id)
        if dataset is None:
            return ToolResult(success=False, error_code="headless_dataset_not_found", message="当前数据源未绑定可用 Headless 数据集")
        try:
            schema = HeadlessSchemaBuilder(self.session).build_dataset_schema(oid, dataset.id)
            result = self.compiler.compile(
                SemanticSQLCompileRequest(
                    schema=schema,
                    question=payload.get("question") or payload.get("origin_question") or "",
                    slots=payload.get("slots") or {},
                    limit=payload.get("limit"),
                )
            )
        except ValueError as exc:
            return ToolResult(success=False, error_code=str(exc), message="语义资产不足，无法使用规则编译生成 SQL")
        return ToolResult(
            success=True,
            payload={
                "sql": result.sql,
                "tables": result.tables,
                "metrics": result.metrics,
                "dimensions": result.dimensions,
                "dataset_id": dataset.id,
                "strategy": "semantic_sql_compiler",
            },
        )

    def _resolve_dataset(self, oid: int, datasource_id: int) -> HeadlessDataSet | None:
        datasets = self.session.exec(
            select(HeadlessDataSet).where(HeadlessDataSet.oid == oid, HeadlessDataSet.status == 1).order_by(HeadlessDataSet.id)
        ).scalars().all()
        if not datasets:
            return None
        models = self.session.exec(
            select(HeadlessModel).where(
                HeadlessModel.oid == oid,
                HeadlessModel.datasource_id == datasource_id,
                HeadlessModel.status == 1,
            )
        ).scalars().all()
        model_ids = {model.id for model in models}
        for dataset in datasets:
            configs = (dataset.data_set_detail or {}).get("dataSetModelConfigs") or []
            if any(config.get("id") in model_ids for config in configs if isinstance(config, dict)):
                return dataset
        return None
