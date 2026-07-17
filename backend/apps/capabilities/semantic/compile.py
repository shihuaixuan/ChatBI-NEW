"""语义编译请求组装：数据集解析 + 结构化查询计划 → SemanticSQLCompiler。

图侧 SqlAdapter 与 Agentic 工具层共用的编译入口（当前图侧仍走自身实现，
按解耦分析 Step 3 收敛后统一到此处）。入参为普通领域对象。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from apps.capabilities.schemas import ToolResult
from apps.headless.models import HeadlessDataSet, HeadlessModel
from apps.headless.service import HeadlessSchemaBuilder
from apps.headless.sql_compiler import SemanticSQLCompiler, SemanticSQLCompileRequest


def resolve_dataset_by_datasource(session, oid: int, datasource_id: int) -> HeadlessDataSet | None:
    """按数据源解析可用的 Headless 数据集（逻辑移自 v1 SemanticSQLCompilerTool）。"""

    datasets = session.exec(
        select(HeadlessDataSet).where(HeadlessDataSet.oid == oid, HeadlessDataSet.status == 1).order_by(HeadlessDataSet.id)
    ).scalars().all()
    if not datasets:
        return None
    models = session.exec(
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


def compile_semantic_sql(
    session,
    *,
    oid: int,
    question: str = "",
    slots: dict[str, Any] | None = None,
    limit: int | None = None,
    dataset_id: int | None = None,
    datasource_id: int | None = None,
    compiler: SemanticSQLCompiler | None = None,
) -> ToolResult:
    """编译语义 SQL。dataset_id 与 datasource_id 至少给一个；前者优先。"""

    if dataset_id is None:
        if not datasource_id:
            return ToolResult(success=False, error_code="dataset_or_datasource_required", message="缺少数据集或数据源")
        dataset = resolve_dataset_by_datasource(session, oid=oid, datasource_id=datasource_id)
        if dataset is None:
            return ToolResult(
                success=False,
                error_code="headless_dataset_not_found",
                message="当前数据源未绑定可用 Headless 数据集",
            )
        dataset_id = dataset.id
    try:
        schema = HeadlessSchemaBuilder(session).build_dataset_schema(oid, dataset_id)
        result = (compiler or SemanticSQLCompiler()).compile(
            SemanticSQLCompileRequest(
                schema=schema,
                question=question or "",
                slots=slots or {},
                limit=limit,
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
            "dataset_id": dataset_id,
            "strategy": "semantic_sql_compiler",
        },
    )
