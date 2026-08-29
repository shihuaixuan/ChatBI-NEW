"""根据语义绑定结果冻结统一 Agent 执行需求。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chatbi.errors import ResearchRequirementError
from apps.chatbi.models.dto.execution_requirement import (
    ExecutionRequirement,
    ExecutionRoute,
)
from apps.chatbi.models.dto.research_agent import ResearchBudget
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.services.research.routing_freeze import freeze_research_requirement
from apps.semantic.models.dto import DatasetSchema, SchemaElement
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.temporal import TemporalContext


class ExecutionRequirementBuildError(ValueError):
    """无法生成合法的冻结执行需求。"""


class ModeRoutingError(ValueError):
    """无法生成可执行需求，或请求的模式不允许。"""


@dataclass(frozen=True, slots=True)
class ExecutionRequirementInput:
    """已经校验过的语义解析结果和候选资产。"""

    semantic_parse: SemanticParseOutput
    candidate_groups: dict[str, list[dict[str, Any]]]
    dataset_id: int
    tenant_id: int
    goal: str = ""
    temporal_context: TemporalContext | None = None
    datasource_id: int | None = None
    user_id: int | None = None
    permission_version: str | None = None
    authorized_tables: tuple[str, ...] = ()
    research_budget: ResearchBudget | None = None


class ExecutionRequirementBuilder:
    """补齐资产定义并冻结统一 Plan-and-Solve Agent 输入。"""

    def __init__(self, schema_provider: DatasetSchemaProvider) -> None:
        if schema_provider is None:
            raise ValueError("EXECUTION_REQUIREMENT_SCHEMA_PROVIDER_REQUIRED")
        self._schema_provider = schema_provider

    def build(self, request: ExecutionRequirementInput) -> dict[str, Any]:
        """冻结目标、Scope、权限、版本和预算，不生成固定执行 DAG。"""

        semantic_parse = request.semantic_parse
        if semantic_parse.status != "resolved" or semantic_parse.unresolved:
            raise ExecutionRequirementBuildError("SEMANTIC_PARSE_NOT_RESOLVED")
        schema = self._schema_provider.build_dataset_schema(
            request.tenant_id,
            request.dataset_id,
        )
        selected_refs = set(_selected_refs(semantic_parse))
        self._validate_selected_candidates(request, schema, selected_refs)
        try:
            requirement = freeze_research_requirement(
                semantic_parse=semantic_parse,
                schema=schema,
                temporal_context=request.temporal_context,
                budget=request.research_budget,
                tenant_scope=f"oid:{int(request.tenant_id)}",
                dataset_ref=f"ASSET:dataset:{request.dataset_id or 0}",
                user_id=request.user_id,
                datasource_id=request.datasource_id,
                permission_version=request.permission_version,
                authorized_tables=request.authorized_tables,
                goal=request.goal,
            )
        except ResearchRequirementError as exc:
            raise ExecutionRequirementBuildError(exc.code) from exc
        except ValueError as exc:
            raise ExecutionRequirementBuildError(
                f"RESEARCH_REQUIREMENT_INVALID:{exc}"
            ) from exc

        required_refs = {
            *requirement.target_metric_refs,
            *requirement.scope.dimension_refs,
            *requirement.scope.driver_metric_refs,
            *(item.target_ref for item in requirement.immutable_filters),
        }
        schema_elements = _schema_elements(schema)
        research_assets = {
            ref: {
                **_asset_definition(schema_elements[ref]),
                "description": str(schema_elements[ref].description or ""),
            }
            for ref in sorted(required_refs)
            if ref in schema_elements
        }
        missing_assets = sorted(required_refs - set(research_assets))
        if missing_assets:
            raise ExecutionRequirementBuildError(
                "RESEARCH_ASSET_SNAPSHOT_INCOMPLETE:" + ",".join(missing_assets)
            )

        return ExecutionRequirement(
            status="ready",
            route=ExecutionRoute(
                # 仅用于快照审计，Runtime 不读取该字段进行分发。
                mode="research",
                reasons=("unified_research_react",),
            ),
            runtime={
                "tenant_id": request.tenant_id,
                "datasource_id": request.datasource_id,
                "dataset_id": request.dataset_id,
                "schema_version": schema.schema_version,
                "contract_version": schema.contract_version,
            },
            asset_snapshot={
                "schema_version": schema.schema_version,
                "contract_version": schema.contract_version,
                "schema_fingerprint": schema.schema_fingerprint,
                "dataset_schema": schema.model_dump(mode="json"),
                "research_assets": research_assets,
            },
            research_requirement=requirement.model_dump(mode="json"),
        ).model_dump(mode="json")

    @staticmethod
    def _validate_selected_candidates(
        request: ExecutionRequirementInput,
        schema: DatasetSchema,
        selected_refs: set[str],
    ) -> None:
        """确认模型选择的候选资产属于当前发布 Schema。"""

        schema_elements = _schema_elements(schema)
        candidate_refs = {
            str(candidate["ref"])
            for candidates in request.candidate_groups.values()
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("ref")
        }
        missing_candidates = sorted(selected_refs - candidate_refs)
        if missing_candidates:
            raise ExecutionRequirementBuildError(
                "SEMANTIC_PARSE_CANDIDATE_NOT_FOUND:" + ",".join(missing_candidates)
            )
        missing_definitions = sorted(selected_refs - set(schema_elements))
        if missing_definitions:
            raise ExecutionRequirementBuildError(
                "SEMANTIC_ASSET_DEFINITION_NOT_FOUND:" + ",".join(missing_definitions)
            )


def _selected_refs(semantic_parse: SemanticParseOutput) -> tuple[str, ...]:
    """收集语义解析中实际选择的治理资产引用。"""

    return tuple(
        dict.fromkeys(
            (
                *(item.ref for item in semantic_parse.measures),
                *semantic_parse.dimension_group_refs(),
                *(item.target_ref for item in semantic_parse.filters),
                *(
                    item.target_ref
                    for item in semantic_parse.operations
                    if item.type == "sort" and item.target_ref is not None
                ),
            )
        )
    )


def _schema_elements(schema: DatasetSchema) -> dict[str, SchemaElement]:
    return {
        **{f"METRIC:{item.id}:{item.model}": item for item in schema.metrics},
        **{f"DIMENSION:{item.id}:{item.model}": item for item in schema.dimensions},
    }


def _asset_definition(element: SchemaElement) -> dict[str, Any]:
    """把 SchemaElement 转为冻结快照中的治理资产定义。"""

    if element.model is None:
        raise ExecutionRequirementBuildError(
            f"SEMANTIC_ASSET_MODEL_REQUIRED:{element.id}"
        )
    if element.type == "METRIC":
        expression = _metric_expression(element)
        if not expression:
            raise ExecutionRequirementBuildError(
                f"SEMANTIC_METRIC_EXPRESSION_REQUIRED:{element.id}"
            )
        return {
            "ref": f"METRIC:{element.id}:{element.model}",
            "model_ref": f"MODEL:{element.model}",
            "model_id": element.model,
            "asset_type": element.type,
            "asset_id": element.id,
            "display_name": element.name,
            "biz_name": element.biz_name,
            "expression": expression,
        }
    field = str(element.ext_info.get("field_name") or "").strip()
    if not field:
        raise ExecutionRequirementBuildError(
            f"SEMANTIC_DIMENSION_FIELD_REQUIRED:{element.id}"
        )
    return {
        "ref": f"DIMENSION:{element.id}:{element.model}",
        "model_ref": f"MODEL:{element.model}",
        "model_id": element.model,
        "asset_type": element.type,
        "asset_id": element.id,
        "display_name": element.name,
        "biz_name": element.biz_name,
        "column": field,
    }


def _metric_expression(element: SchemaElement) -> str:
    params = element.type_params
    define_type = str(params.get("metricDefineType") or "").upper()
    if define_type == "FIELD":
        return str(
            (params.get("metricDefineByFieldParams") or {}).get("expr") or ""
        ).strip()
    if define_type == "METRIC":
        return str(
            (params.get("metricDefineByMetricParams") or {}).get("expr") or ""
        ).strip()
    measure_params = params.get("metricDefineByMeasureParams") or {}
    if not isinstance(measure_params, dict):
        return ""
    measures = measure_params.get("measures")
    expressions = [
        str(item.get("expr") or "").strip()
        for item in (measures if isinstance(measures, list) else [])
        if isinstance(item, dict) and str(item.get("expr") or "").strip()
    ]
    if len(expressions) == 1:
        return expressions[0]
    return str(measure_params.get("expr") or "").strip()


__all__ = [
    "ExecutionRequirementBuildError",
    "ModeRoutingError",
    "ExecutionRequirementBuilder",
    "ExecutionRequirementInput",
]
