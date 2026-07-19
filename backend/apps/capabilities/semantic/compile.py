"""旧语义编译函数兼容入口，实际编译统一转发到 Semantic Service。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from apps.capabilities.schemas import ToolResult
from apps.semantic.models.dto import SemanticQueryCompileRequest
from apps.semantic.models.orm import SemanticDataset, SemanticModel
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.semantic.services.sql_compilation_service import (
    SemanticSQLCompilationService,
)
from apps.semantic.services.sql_compiler import (
    SemanticSQLCompiler,
)


def resolve_dataset_by_datasource(session, oid: int, datasource_id: int) -> SemanticDataset | None:
    """按数据源解析可用的 Semantic 数据集（逻辑移自 v1 SemanticSQLCompilerTool）。"""

    datasets = session.exec(
        select(SemanticDataset).where(SemanticDataset.oid == oid, SemanticDataset.status == 1).order_by(SemanticDataset.id)
    ).scalars().all()
    if not datasets:
        return None
    models = session.exec(
        select(SemanticModel).where(
            SemanticModel.oid == oid,
            SemanticModel.datasource_id == datasource_id,
            SemanticModel.status == 1,
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
                error_code="semantic_dataset_not_found",
                message="当前数据源未绑定可用 Semantic 数据集",
            )
        dataset_id = dataset.id
    try:
        result = SemanticSQLCompilationService(
            SemanticSchemaService(SemanticSchemaLoader(session)),
            compiler or SemanticSQLCompiler(),
        ).compile(
            SemanticQueryCompileRequest(
                workspace_id=oid,
                dataset_id=dataset_id,
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
            "dataset_id": result.dataset_id,
            "strategy": "semantic_sql_compiler",
        },
    )
