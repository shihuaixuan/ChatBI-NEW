"""Semantic 公共 Tool。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import (
    DEFAULT_REF_TEMPLATE,
    GenerateJsonSchema,
    JsonSchemaMode,
)

from apps.datasource import DatasourceQueryService, DatasourceQuerySubject
from apps.retrieval import (
    ExecutableAssetReference,
    RetrievalPermissionError,
    RetrievalQueryError,
    RetrievalService,
    filter_semantic_payload_tables,
    is_compilation_decision_executable,
    validate_compilation_allowlist,
)
from apps.semantic import (
    DatasetSchema,
    DatasetSchemaProvider,
    SemanticPlanValidationReport,
    SemanticQueryCompileRequest,
    SemanticQueryPlan,
    SemanticSQLCompilationService,
    SemanticUsedAsset,
    SemanticValidationError,
)
from apps.tool.base import (
    Tool,
    ToolConcurrency,
    ToolExecutionPolicy,
    json_summary,
)
from apps.tool.context import current_tool_call_context
from apps.tool.result import RetryAdvice, ToolErrorCategory, ToolResult
from apps.tool.tools.context import TrustedToolContext
from apps.tool.tools.semantic_contracts import (
    SemanticAssetScope,
    SemanticToolContext,
    project_semantic_query_plans,
)


class TermQueryService(Protocol):
    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[Any]: ...


class SearchTerminologyArgs(BaseModel):
    term: str = Field(min_length=1, description="要查询的业务术语或口语说法")


class SearchTerminologyResult(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class SearchTerminologyTool(
    Tool[TrustedToolContext, SearchTerminologyArgs, SearchTerminologyResult]
):
    name = "search_terminology"
    description = "查询业务术语的解释与映射，用于理解问题中的业务词、缩写和同义词。"
    args_model = SearchTerminologyArgs
    result_model = SearchTerminologyResult
    execution = ToolExecutionPolicy(timeout_seconds=20)

    def __init__(self, term_query_service: TermQueryService) -> None:
        if term_query_service is None:
            raise ValueError("SEMANTIC_TERM_QUERY_SERVICE_REQUIRED")
        self._term_query_service = term_query_service

    def execute(
        self,
        ctx: TrustedToolContext,
        args: SearchTerminologyArgs,
    ) -> ToolResult[SearchTerminologyResult]:
        if ctx.dataset_id is None or ctx.dataset_id <= 0:
            return ToolResult.failed(
                "当前问数记录没有绑定 Semantic 数据集，无法查询业务术语。",
                error_code="semantic_dataset_not_found",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        results = self._term_query_service.search(
            ctx.workspace_id,
            ctx.dataset_id,
            args.term,
            limit=10,
        )
        items = [result.model_dump(mode="json") for result in results]
        data = SearchTerminologyResult(items=items, count=len(items))
        if not results:
            return ToolResult.succeeded(
                f"术语库中未找到与「{args.term}」相关的条目。",
                data,
            )
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


class SearchSemanticAssetsArgs(BaseModel):
    """检索输入由 Agent 运行层确认，模型调用时不重复提交。"""

    model_config = ConfigDict(extra="forbid")


class SemanticAssetPackage(BaseModel):
    """提供给模型和 Agent 结果处理器的语义检索包。"""

    model_config = ConfigDict(extra="allow")

    hit: bool = False
    status: str | None = None
    dataset_id: int | None = None
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    # verified query 只提供问题与语义计划摘要，不向模型暴露裸 SQL。
    examples: list[dict[str, Any]] = Field(default_factory=list)
    candidate_groups: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    selected_assets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    slot_bindings: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    multi_query_plans: list[dict[str, Any]] = Field(default_factory=list)
    allowed_asset_ids: list[ExecutableAssetReference] = Field(default_factory=list)


class SearchSemanticAssetsResult(BaseModel):
    package: SemanticAssetPackage
    scope: SemanticAssetScope


class SearchSemanticAssetsTool(
    Tool[
        SemanticToolContext,
        SearchSemanticAssetsArgs,
        SearchSemanticAssetsResult,
    ]
):
    name = "search_semantic_assets"
    description = (
        "按问题重写模型输出的指标短语和维度短语检索候选资产，无需传入参数。"
        "该工具只返回候选指标和维度，返回候选结果后结束。"
    )
    args_model = SearchSemanticAssetsArgs
    result_model = SearchSemanticAssetsResult
    execution = ToolExecutionPolicy(
        concurrency=ToolConcurrency.PARALLEL_SAFE,
        timeout_seconds=30,
    )

    def __init__(
        self,
        retrieval_service: RetrievalService,
        query_service: DatasourceQueryService,
        schema_provider: DatasetSchemaProvider | None = None,
    ) -> None:
        if retrieval_service is None:
            raise ValueError("SEMANTIC_RETRIEVAL_SERVICE_REQUIRED")
        if query_service is None:
            raise ValueError("DATASOURCE_QUERY_SERVICE_REQUIRED")
        self._retrieval_service = retrieval_service
        self._query_service = query_service
        self._schema_provider = schema_provider

    def execute(
        self,
        ctx: SemanticToolContext,
        args: SearchSemanticAssetsArgs,
    ) -> ToolResult[SearchSemanticAssetsResult]:
        if ctx.dataset_id is None or ctx.dataset_id <= 0:
            return ToolResult.failed(
                "当前数据源未绑定可用的语义数据集，无法进行语义检索。可改用 get_dataset_schema 查看物理表结构。",
                error_code="semantic_dataset_not_found",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        if ctx.user_id is None or ctx.user_id <= 0 or ctx.datasource_id is None:
            return ToolResult.failed(
                "Datasource 权限服务或可信身份未配置。",
                error_code="query_policy_service_required",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        request = ctx.semantic_retrieval_request
        if request is None:
            return ToolResult.rejected(
                "缺少已确认的语义检索请求，禁止在工具选择阶段重新生成检索意图。",
                error_code="semantic_retrieval_request_required",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if (
            request.tenant_id != ctx.workspace_id
            or request.actor_id != ctx.user_id
            or request.scope.dataset_ids != [ctx.dataset_id]
        ):
            return ToolResult.rejected(
                "语义检索请求与当前可信身份或数据集不一致。",
                error_code="semantic_retrieval_scope_mismatch",
                error_category=ToolErrorCategory.SAFETY,
            )

        policy = self._query_service.resolve_policy(
            DatasourceQuerySubject(
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
            ),
            ctx.datasource_id,
        )
        if not policy.allowed or not policy.authorized_tables:
            return ToolResult.rejected(
                policy.reason or "当前身份没有可访问的表。",
                error_code=policy.error_code or "authorized_tables_empty",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        call_context = current_tool_call_context()
        remaining = (
            call_context.remaining_seconds() if call_context is not None else None
        )
        try:
            if remaining is None:
                retrieval = self._retrieval_service.retrieve(request)
            else:
                retrieval = self._retrieval_service.retrieve(
                    request,
                    timeout_ms=max(1, int(remaining * 1000)),
                )
        except TimeoutError:
            return ToolResult.failed(
                "语义检索超过有效截止时间。",
                error_code="semantic_retrieval_timeout",
                error_category=ToolErrorCategory.TIMEOUT,
                retry_advice=RetryAdvice.SAME_INPUT,
            )
        except RetrievalQueryError as exc:
            reason_code = str(exc.details.get("reason_code") or "")
            error_code = {
                "SEMANTIC_METRIC_DIMENSION_INCOMPATIBLE": (
                    "semantic_metric_dimension_incompatible"
                ),
            }.get(reason_code, "semantic_retrieval_query_invalid")
            return ToolResult.rejected(
                "语义检索请求无法生成可执行的资产组合。",
                error_code=error_code,
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details={
                    "retrieval_error_code": exc.code,
                    **exc.details,
                },
            )

        authorized = {table.lower() for table in policy.authorized_tables}
        retrieved_tables = [
            str(table) for table in retrieval.payload.get("tables") or []
        ]
        unauthorized = [
            table for table in retrieved_tables if table.lower() not in authorized
        ]
        if unauthorized:
            return ToolResult.rejected(
                f"语义检索结果包含无权访问的表: {sorted(unauthorized)}。",
                error_code="semantic_table_out_of_scope",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )

        package = project_semantic_package(retrieval.payload, authorized)
        scope = SemanticAssetScope(
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            datasource_id=ctx.datasource_id,
            dataset_id=ctx.dataset_id,
            retrieval_id=request.request_id,
            decision_status=None,
            allowed_assets=(),
            authorized_tables=tuple(retrieved_tables),
            normalized_time_range=None,
            compile_plan=None,
            permission_version=request.scope.permission_version,
        )
        data = SearchSemanticAssetsResult(package=package, scope=scope)
        metadata = {
            "semantic_payload": retrieval.payload,
            "semantic_retrieval_request": request.model_dump(mode="json"),
            "semantic_retrieval_filters": getattr(retrieval, "filters", {}),
        }
        return ToolResult.succeeded(
            json_summary(package.model_dump(mode="json"), ctx.summary_max_chars),
            data,
            metadata=metadata,
        )


def _project_strict_query_plans(
    schema: DatasetSchema,
    package: SemanticAssetPackage,
    intent: dict[str, Any],
) -> list[tuple[SemanticQueryPlan, SemanticPlanValidationReport]]:
    """为严格模式的单查询或 CROSS_MODEL 子查询分别生成验证计划。"""

    return list(
        project_semantic_query_plans(
            schema,
            package.slot_bindings,
            intent,
            package.multi_query_plans,
        )
    )


class CompileFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(description="过滤维度的 asset_id，必须来自语义包")
    operator: str = Field(
        default="=",
        description="过滤操作符，如 = / != / > / >= / < / <= / in / like",
    )
    value: (
        str | int | float | bool | list[str | int | float | bool] | dict[str, Any]
    ) = Field(description="过滤值；时间筛选必须使用已确认的归一化时间范围")


class CompileOrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: int | None = Field(
        default=None,
        description="排序指标或维度的 asset_id，优先使用可信查询计划提供的值",
    )
    biz_name: str | None = Field(
        default=None,
        description="兼容旧调用的排序字段 biz_name，只能匹配已选择资产",
    )
    direction: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def validate_target(self) -> CompileOrderBy:
        if self.asset_id is None and not str(self.biz_name or "").strip():
            raise ValueError("排序条件必须提供 asset_id 或 biz_name")
        return self


class CompileSemanticSqlArgs(BaseModel):
    """把结构化查询计划确定性编译为 SQL。"""

    model_config = ConfigDict(extra="forbid")

    plan_fingerprint: str | None = Field(
        default=None,
        description="已验证查询方案的内部编号，通常由系统自动填写，模型不需要自行生成",
    )
    # 迁移期 LEGACY 数据集仍使用旧参数；严格模式的工具 Schema 不暴露这些字段。
    metric_asset_ids: list[int] | None = Field(default=None)
    dimension_asset_ids: list[int] | None = Field(default=None)
    filters: list[CompileFilter] | None = Field(default=None)
    time_bucket: dict[str, Any] | None = Field(default=None)
    order_by: list[CompileOrderBy] | None = Field(default=None)
    limit: int | None = Field(default=None)
    having: list[dict[str, Any]] | None = Field(default=None)
    time_offset: dict[str, Any] | None = Field(default=None)
    output_aliases: dict[int, str] = Field(default_factory=dict)

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = DEFAULT_REF_TEMPLATE,
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        """对模型只公开严格模式的计划指纹参数。"""

        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        properties = schema.get("properties") or {}
        schema["properties"] = {"plan_fingerprint": properties["plan_fingerprint"]}
        return schema


class CompileSemanticSqlResult(BaseModel):
    sql: str
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    dataset_id: int
    datasource_id: int | None = None
    used_assets: list[SemanticUsedAsset] = Field(default_factory=list)
    strategy: str


class CompileSemanticSqlTool(
    Tool[
        SemanticToolContext,
        CompileSemanticSqlArgs,
        CompileSemanticSqlResult,
    ]
):
    name = "compile_semantic_sql"
    description = (
        "编译已经确认并验证通过的查询方案。调用时不要自行填写指标、维度、过滤、时间和计算规则；"
        "系统会自动使用已经确认的方案。"
    )
    args_model = CompileSemanticSqlArgs
    result_model = CompileSemanticSqlResult
    execution = ToolExecutionPolicy(timeout_seconds=30)

    def __init__(
        self,
        compilation_service: SemanticSQLCompilationService,
        query_service: DatasourceQueryService,
    ) -> None:
        if compilation_service is None:
            raise ValueError("SEMANTIC_COMPILATION_SERVICE_REQUIRED")
        if query_service is None:
            raise ValueError("DATASOURCE_QUERY_SERVICE_REQUIRED")
        self._compilation_service = compilation_service
        self._query_service = query_service

    def prepare_args(
        self,
        ctx: SemanticToolContext,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """强制使用当前运行中保存的计划指纹，禁止模型替换语义绑定。"""

        scope = ctx.semantic_asset_scope
        if scope is None or scope.query_plan is None:
            if (
                scope is None
                or not is_compilation_decision_executable(scope.decision_status)
                or scope.compile_plan is None
                or not scope.compile_plan.metric_asset_ids
            ):
                return dict(args)
            prepared = dict(args)
            trusted_plan = scope.compile_plan.model_dump(
                mode="json",
                include={
                    "metric_asset_ids",
                    "dimension_asset_ids",
                    "order_by",
                    "limit",
                    "having",
                    "time_offset",
                    "output_aliases",
                },
            )
            trusted_plan["filters"] = [
                item.model_dump(mode="json")
                for item in (
                    *scope.compile_plan.filters,
                    *scope.compile_plan.temporal_plan.filters,
                )
            ]
            time_bucket = scope.compile_plan.temporal_plan.time_bucket
            trusted_plan["time_bucket"] = (
                time_bucket.model_dump(mode="json")
                if time_bucket is not None
                else None
            )
            prepared.update(trusted_plan)
            return prepared
        return {"plan_fingerprint": scope.query_plan.fingerprint}

    def execute(
        self,
        ctx: SemanticToolContext,
        args: CompileSemanticSqlArgs,
    ) -> ToolResult[CompileSemanticSqlResult]:
        args = CompileSemanticSqlArgs.model_validate(
            self.prepare_args(ctx, args.model_dump(mode="json"))
        )
        scope = ctx.semantic_asset_scope
        if scope is None:
            return ToolResult.rejected(
                "尚未检索语义资产，请先调用 search_semantic_assets。",
                error_code="semantic_package_required",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if scope.semantic_enforcement == "STRICT":
            return self._execute_strict(ctx, args, scope)
        if (
            not is_compilation_decision_executable(scope.decision_status)
        ):
            return ToolResult.rejected(
                "语义决策尚未收敛，禁止生成 SQL。",
                error_code="semantic_decision_not_executable",
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details={
                    "decision_status": (
                        scope.decision_status.value
                        if scope.decision_status is not None
                        else "candidate_only"
                    ),
                    "retry_action": "clarify_semantic_binding",
                },
            )
        plan_error = _validate_compile_plan(scope.compile_plan)
        if plan_error is not None:
            return ToolResult.rejected(
                "语义查询计划未覆盖已确认的分析形态，禁止生成不完整 SQL。",
                error_code="semantic_query_plan_incomplete",
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details=plan_error,
            )
        if not _scope_matches_context(ctx, scope):
            return ToolResult.rejected(
                "语义资产范围与当前可信身份、数据源或数据集不一致。",
                error_code="semantic_scope_mismatch",
                error_category=ToolErrorCategory.SAFETY,
            )
        assert ctx.user_id is not None
        assert ctx.datasource_id is not None
        policy = self._query_service.resolve_policy(
            DatasourceQuerySubject(
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
            ),
            ctx.datasource_id,
        )
        current_tables = {table.lower() for table in policy.authorized_tables}
        if (
            not policy.allowed
            or not current_tables
            or any(
                table.lower() not in current_tables for table in scope.authorized_tables
            )
        ):
            return ToolResult.rejected(
                policy.reason or "语义检索后的数据权限已经变化，请重新检索。",
                error_code=policy.error_code or "semantic_scope_permission_changed",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )

        dimension_ids = [
            *(args.dimension_asset_ids or []),
            *(item.asset_id for item in (args.filters or [])),
        ]
        if args.time_bucket and isinstance(args.time_bucket.get("dimension_id"), int):
            dimension_ids.append(args.time_bucket["dimension_id"])
        try:
            validate_compilation_allowlist(
                scope.decision_status,
                scope.allowed_assets,
                metric_ids=args.metric_asset_ids or [],
                dimension_ids=dimension_ids,
            )
        except RetrievalPermissionError as exc:
            denied_assets = exc.details.get("denied_assets") or []
            return ToolResult.rejected(
                f"{exc}: {denied_assets}",
                error_code="asset_not_in_package",
                error_category=ToolErrorCategory.SAFETY,
            )
        except RetrievalQueryError as exc:
            details = {
                **exc.details,
                "retry_action": "clarify_semantic_binding",
            }
            return ToolResult.rejected(
                f"{exc}；当前语义决策尚不可执行，请完成语义澄清后再编译。",
                error_code="semantic_decision_not_executable",
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details=details,
            )

        filters = [item.model_dump(mode="json") for item in (args.filters or [])]
        if scope.normalized_time_range is not None:
            normalized_time = scope.normalized_time_range
            if normalized_time.get("kind") == "unsupported":
                return ToolResult.rejected(
                    "已确认时间范围尚未归一化，禁止生成 SQL。",
                    error_code="time_range_unsupported",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                )
            matched_time_filter = False
            for filter_item in filters:
                value = filter_item["value"]
                if value == normalized_time:
                    filter_item["value"] = normalized_time
                    matched_time_filter = True
            if not matched_time_filter:
                return ToolResult.rejected(
                    "时间筛选与已确认问题不一致，禁止省略时间或替换成数据最大日期。",
                    error_code="time_filter_mismatch",
                    error_category=ToolErrorCategory.SAFETY,
                )

        slots: dict[str, Any] = {
            "metrics": [
                {"asset_id": asset_id, "asset_type": "METRIC"}
                for asset_id in (args.metric_asset_ids or [])
            ],
            "dimensions": [
                {"asset_id": asset_id, "asset_type": "DIMENSION"}
                for asset_id in (args.dimension_asset_ids or [])
            ],
            "filters": [
                {**filter_item, "asset_type": "DIMENSION"} for filter_item in filters
            ],
        }
        try:
            compiled = self._compilation_service.compile(
                SemanticQueryCompileRequest(
                    workspace_id=ctx.workspace_id,
                    dataset_id=scope.dataset_id,
                    # 候选检索请求只保存短语；完整重写问题从可信上下文读取。
                    question=ctx.rewritten_question,
                    slots=slots,
                    order_by=[
                        item.model_dump(mode="json")
                        for item in (args.order_by or [])
                    ],
                    limit=args.limit or ctx.semantic_default_limit,
                    time_bucket=args.time_bucket,
                    time_offset=args.time_offset,
                    having=args.having or [],
                    output_aliases=args.output_aliases,
                )
            )
        except (SemanticValidationError, ValueError) as exc:
            return ToolResult.failed(
                "语义资产不足，无法使用规则编译生成 SQL",
                error_code=str(exc),
                error_category=ToolErrorCategory.DOMAIN,
                retry_advice=RetryAdvice.CORRECT_INPUT,
            )
        if compiled.datasource_id not in {None, ctx.datasource_id}:
            return ToolResult.rejected(
                "语义编译结果使用了当前范围之外的数据源。",
                error_code="compiled_datasource_out_of_scope",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        if any(table.lower() not in current_tables for table in compiled.tables):
            return ToolResult.rejected(
                "语义编译结果使用了当前范围之外的数据表。",
                error_code="compiled_table_out_of_scope",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        coverage_error = _validate_compiled_plan_coverage(
            compiled.sql,
            compiled.used_assets,
            scope.compile_plan,
        )
        if coverage_error is not None:
            return ToolResult.rejected(
                "编译 SQL 未完整覆盖可信查询计划，禁止进入执行阶段。",
                error_code="compiled_query_plan_not_covered",
                error_category=ToolErrorCategory.SAFETY,
                details=coverage_error,
            )
        data = CompileSemanticSqlResult(
            sql=compiled.sql,
            tables=compiled.tables,
            metrics=compiled.metrics,
            dimensions=compiled.dimensions,
            dataset_id=compiled.dataset_id,
            datasource_id=compiled.datasource_id,
            used_assets=compiled.used_assets,
            strategy="semantic_sql_compiler",
        )
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )

    def _execute_strict(
        self,
        ctx: SemanticToolContext,
        args: CompileSemanticSqlArgs,
        scope: SemanticAssetScope,
    ) -> ToolResult[CompileSemanticSqlResult]:
        """严格模式只消费验证通过且指纹一致的完整语义计划。"""

        plan = scope.query_plan
        report = _validation_report_for_plan(scope, plan)
        if plan is None or report is None:
            return ToolResult.rejected(
                "严格语义范围缺少完整查询计划或验证报告。",
                error_code="semantic_query_plan_required",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if args.plan_fingerprint != plan.fingerprint:
            return ToolResult.rejected(
                "提交的计划指纹与当前 Agent Run 不一致。",
                error_code="semantic_query_plan_fingerprint_mismatch",
                error_category=ToolErrorCategory.SAFETY,
            )
        if plan.validation_status.value != "PROVEN" or report.status.value != "PROVEN":
            return ToolResult.rejected(
                "语义查询计划尚未通过确定性验证，禁止编译。",
                error_code="semantic_query_plan_not_proven",
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details={
                    "status": plan.validation_status.value,
                    "reason_codes": list(report.reason_codes),
                },
            )
        strict_compile_contract = _strict_plan_compile_contract(plan)
        plan_error = _validate_compile_plan(strict_compile_contract)
        if plan_error is not None:
            return ToolResult.rejected(
                "严格语义查询计划未覆盖已确认的分析形态。",
                error_code="semantic_query_plan_incomplete",
                error_category=ToolErrorCategory.BUSINESS_RULE,
                details=plan_error,
            )
        if not _scope_matches_context(ctx, scope):
            return ToolResult.rejected(
                "语义资产范围与当前可信身份、数据源或数据集不一致。",
                error_code="semantic_scope_mismatch",
                error_category=ToolErrorCategory.SAFETY,
            )
        assert ctx.user_id is not None
        assert ctx.datasource_id is not None
        policy = self._query_service.resolve_policy(
            DatasourceQuerySubject(
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
            ),
            ctx.datasource_id,
        )
        if not policy.allowed or any(
            table.lower() not in {item.lower() for item in policy.authorized_tables}
            for table in scope.authorized_tables
        ):
            return ToolResult.rejected(
                policy.reason or "语义计划所需的数据权限已经变化，请重新检索。",
                error_code=policy.error_code or "semantic_scope_permission_changed",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        try:
            compiled = self._compilation_service.compile_verified_plan(
                ctx.workspace_id,
                plan,
                schema_snapshot=scope.schema_snapshot,
            )
        except (SemanticValidationError, ValueError) as exc:
            # 严格入口不把编译错误转换为替换资产或改写计划的机会。
            return ToolResult.failed(
                "严格语义查询计划编译失败，未修改查询口径。",
                error_code=str(exc),
                error_category=ToolErrorCategory.DOMAIN,
                retry_advice=RetryAdvice.NEVER,
            )
        current_tables = {table.lower() for table in policy.authorized_tables}
        if any(table.lower() not in current_tables for table in compiled.tables):
            return ToolResult.rejected(
                "语义编译结果使用了当前权限之外的数据表。",
                error_code="compiled_table_out_of_scope",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        expected_assets = {
            ("METRIC", item.metric_id) for item in plan.metrics
        } | {
            ("DIMENSION", item.physical_dimension_id) for item in plan.dimensions
        }
        if plan.time_binding.dimension_id is not None:
            expected_assets.add(("DIMENSION", plan.time_binding.dimension_id))
        allowed_assets = {
            (
                str(getattr(item.asset_type, "value", item.asset_type)).upper(),
                item.asset_id,
            )
            for item in scope.allowed_assets
        }
        if not expected_assets <= allowed_assets:
            return ToolResult.rejected(
                "严格语义计划包含当前检索范围之外的资产。",
                error_code="asset_not_in_package",
                error_category=ToolErrorCategory.SAFETY,
                details={"denied_assets": sorted(expected_assets - allowed_assets)},
            )
        actual_assets = {
            (item.asset_type.upper(), item.asset_id) for item in compiled.used_assets
        }
        if not expected_assets.issubset(actual_assets):
            return ToolResult.rejected(
                "编译产物未完整覆盖语义查询计划。",
                error_code="compiled_query_plan_not_covered",
                error_category=ToolErrorCategory.SAFETY,
                details={
                    "missing_assets": sorted(expected_assets - actual_assets),
                    "plan_fingerprint": plan.fingerprint,
                },
            )
        coverage_error = _validate_compiled_plan_coverage(
            compiled.sql,
            compiled.used_assets,
            strict_compile_contract,
        )
        if coverage_error is not None:
            return ToolResult.rejected(
                "编译产物未完整覆盖语义查询计划。",
                error_code="compiled_query_plan_not_covered",
                error_category=ToolErrorCategory.SAFETY,
                details=coverage_error,
            )
        data = CompileSemanticSqlResult(
            sql=compiled.sql,
            tables=compiled.tables,
            metrics=compiled.metrics,
            dimensions=compiled.dimensions,
            dataset_id=compiled.dataset_id,
            datasource_id=compiled.datasource_id,
            used_assets=compiled.used_assets,
            strategy="verified_semantic_query_plan",
        )
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), ctx.summary_max_chars),
            data,
        )


def _validation_report_for_plan(
    scope: SemanticAssetScope,
    plan: SemanticQueryPlan | None,
) -> SemanticPlanValidationReport | None:
    """按计划指纹选择对应报告，避免多查询误用首个子计划报告。"""

    if plan is None:
        return None
    for report in scope.validation_reports:
        if report.evidence.get("plan_fingerprint") == plan.fingerprint:
            return report
    fallback_report = scope.validation_report
    if fallback_report is not None and (
        not fallback_report.evidence
        or fallback_report.evidence.get("plan_fingerprint") == plan.fingerprint
    ):
        return fallback_report
    return None


def _scope_matches_context(
    ctx: SemanticToolContext,
    scope: SemanticAssetScope,
) -> bool:
    return bool(
        ctx.user_id is not None
        and ctx.datasource_id is not None
        and ctx.dataset_id is not None
        and scope.workspace_id == ctx.workspace_id
        and scope.user_id == ctx.user_id
        and scope.datasource_id == ctx.datasource_id
        and scope.dataset_id == ctx.dataset_id
    )


def _validate_compile_plan(
    plan: Any,
) -> dict[str, Any] | None:
    """在编译前检查比较、排名等查询形态必需的计划字段。"""

    if plan is None:
        return None
    missing: list[str] = []
    shape = plan.query_shape if isinstance(plan.query_shape, dict) else {}
    time_bucket = plan.temporal_plan.time_bucket
    if (
        bool(shape.get("needs_group_by"))
        and not plan.dimension_asset_ids
        and time_bucket is None
    ):
        missing.append("group_dimension_asset_ids")
    if str(shape.get("time_grain") or "").strip() and time_bucket is None:
        missing.append("time_bucket")
    if shape.get("comparison_type") in {"yoy", "mom", "custom"} and plan.time_offset is None:
        missing.append("time_offset")
    if bool(shape.get("needs_order_by")) and not plan.order_by:
        missing.append("order_by")
    if plan.intent_type == "ranking_analysis" and plan.limit is None:
        missing.append("limit")
    if not missing:
        return None
    return {
        "intent_type": plan.intent_type,
        "missing_requirements": missing,
        "query_shape": shape,
    }


def _strict_plan_compile_contract(plan: SemanticQueryPlan) -> Any:
    """把严格计划投影为统一覆盖校验需要的最小只读结构。"""

    time_dimension_id = plan.time_binding.dimension_id
    return SimpleNamespace(
        metric_asset_ids=tuple(item.metric_id for item in plan.metrics),
        dimension_asset_ids=tuple(
            item.physical_dimension_id for item in plan.dimensions
        ),
        temporal_plan=SimpleNamespace(
            time_bucket=(
                SimpleNamespace(dimension_id=time_dimension_id)
                if plan.time_binding.grain and time_dimension_id is not None
                else None
            )
        ),
        query_shape=plan.query_shape,
        intent_type=str(plan.query_shape.get("intent_type") or "metric_query"),
        order_by=plan.order_by,
        limit=plan.limit,
    )


def _validate_compiled_plan_coverage(
    sql: str,
    used_assets: list[SemanticUsedAsset],
    plan: Any,
) -> dict[str, Any] | None:
    """检查编译产物是否包含计划要求的指标、分组、排序和 TopN。"""

    if plan is None:
        return None
    used_metric_ids = {
        item.asset_id for item in used_assets if item.asset_type.upper() == "METRIC"
    }
    used_dimension_ids = {
        item.asset_id for item in used_assets if item.asset_type.upper() == "DIMENSION"
    }
    missing: list[str] = []
    for asset_id in plan.metric_asset_ids:
        if asset_id not in used_metric_ids:
            missing.append(f"metric:{asset_id}")
    for asset_id in plan.dimension_asset_ids:
        if asset_id not in used_dimension_ids:
            missing.append(f"group_dimension:{asset_id}")
    time_bucket = plan.temporal_plan.time_bucket
    if time_bucket is not None and time_bucket.dimension_id not in used_dimension_ids:
        missing.append(f"time_bucket_dimension:{time_bucket.dimension_id}")

    normalized_sql = " ".join(sql.lower().split()).rstrip(";")
    shape = plan.query_shape if isinstance(plan.query_shape, dict) else {}
    if bool(shape.get("needs_group_by")) and " group by " not in f" {normalized_sql} ":
        missing.append("group_by")
    if plan.order_by and " order by " not in f" {normalized_sql} ":
        missing.append("order_by")
    if plan.limit is not None and not normalized_sql.endswith(f"limit {plan.limit}"):
        missing.append(f"limit:{plan.limit}")
    if not missing:
        return None
    return {
        "missing_requirements": missing,
        "compiled_sql": sql,
    }


def project_semantic_package(
    payload: dict[str, Any],
    authorized_tables: set[str],
) -> SemanticAssetPackage:
    filtered = filter_semantic_payload_tables(payload, authorized_tables)
    candidate_groups = filtered.get("candidate_groups") or {}
    truncated = {
        group: len(items) - 5
        for group, items in candidate_groups.items()
        if isinstance(items, list) and len(items) > 5
    }

    def _score(item: Any) -> float:
        score = item.get("score") if isinstance(item, dict) else None
        return score if isinstance(score, (int, float)) else float("-inf")

    def _bounded(items: list[Any], limit: int = 5) -> list[Any]:
        # 检索结果按短语子查询拼接，直接截断会让第一个短语的同名跨模型变体
        # 挤掉后续短语的唯一匹配。先按短语分组轮询，保证每个检索短语都有
        # 候选进入截断结果，组内再按融合分数降序。
        by_phrase: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("matched_phrase") or item.get("subquery_id") or "")
            by_phrase.setdefault(key, []).append(item)
        for group_items in by_phrase.values():
            group_items.sort(key=_score, reverse=True)
        bounded: list[dict[str, Any]] = []
        phrases = list(by_phrase)
        while len(bounded) < limit and phrases:
            for key in list(phrases):
                bucket = by_phrase.get(key)
                if not bucket:
                    phrases.remove(key)
                    continue
                bounded.append(bucket.pop(0))
                if len(bounded) >= limit:
                    break
        return bounded

    filtered["candidate_groups"] = {
        group: [_public_candidate(item) for item in _bounded(list(items))]
        for group, items in candidate_groups.items()
        if isinstance(items, list)
    }
    filtered["truncated"] = truncated
    filtered["status"] = _semantic_status(filtered)
    selected_keys = {
        (str(item.get("asset_type") or asset_type), item.get("asset_id"))
        for group, asset_type in (("metrics", "METRIC"), ("dimensions", "DIMENSION"))
        for item in (filtered.get("selected_assets") or {}).get(group, [])
        if isinstance(item, dict) and item.get("asset_id") is not None
    }
    filtered["allowed_asset_ids"] = [
        item
        for item in filtered.get("allowed_asset_ids") or []
        if isinstance(item, dict)
        and (str(item.get("asset_type") or ""), item.get("asset_id")) in selected_keys
    ]
    return SemanticAssetPackage.model_validate(filtered)


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "asset_type",
        "asset_id",
        "biz_name",
        "display_name",
        "score",
        "source",
        "model_id",
        "description",
        "table",
        "table_name",
        "physical_table",
        "matched_text",
        "matched_field",
        "retrieval_scores",
        "retrieval_ranks",
    )
    return {key: item[key] for key in fields if item.get(key) is not None}


def _semantic_status(package: dict[str, Any]) -> str | None:
    decision = package.get("decision")
    if not isinstance(decision, dict):
        return package.get("status")
    reason_codes = {str(code) for code in decision.get("reason_codes") or [] if code}
    if "TIME_DIMENSION_NOT_CONFIGURED_FOR_METRIC_MODEL" in reason_codes:
        return "time_dimension_not_configured"
    if decision.get("status") != "ambiguous":
        return package.get("status")
    ambiguity_types = {
        str(item.get("type") or "")
        for item in package.get("ambiguities") or []
        if isinstance(item, dict) and item.get("type")
    }
    if ambiguity_types == {"metric"}:
        return "metric_ambiguous"
    if ambiguity_types == {"dimension"}:
        return "dimension_ambiguous"
    return "semantic_ambiguous"


__all__ = [
    "CompileFilter",
    "CompileOrderBy",
    "CompileSemanticSqlArgs",
    "CompileSemanticSqlResult",
    "CompileSemanticSqlTool",
    "project_semantic_package",
    "SearchSemanticAssetsArgs",
    "SearchSemanticAssetsResult",
    "SearchSemanticAssetsTool",
    "SearchTerminologyArgs",
    "SearchTerminologyResult",
    "SearchTerminologyTool",
    "SemanticAssetPackage",
    "TermQueryService",
]
