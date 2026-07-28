"""ChatBI 专属语义编排与结束 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from apps.chatbi.models import (
    QueryFinalReplyProjectionData,
    SemanticQueryCompileData,
    SemanticRetrievalData,
)
from apps.chatbi.orchestration.agent.tools.base import (
    AgentTool,
    AgentToolContext,
    QueryService,
    SemanticAssetRetriever,
    SemanticQueryCompiler,
)
from apps.chatbi.services.generation import (
    FinalReplyProjectionError,
    project_query_final_reply,
)
from apps.chatbi.services.planning import SemanticQueryCompileError
from apps.chatbi.services.understanding.time_range import normalize_time_range
from apps.datasource import DatasourceQuerySubject
from apps.tool import (
    RetryAdvice,
    ToolErrorCategory,
    ToolExecutionPolicy,
    ToolResult,
    json_summary,
)

SUMMARY_MAX_CHARS_DEFAULT = 4000


def _summary_limit(ctx: AgentToolContext) -> int:
    return getattr(ctx.config, "summary_max_chars", SUMMARY_MAX_CHARS_DEFAULT)


def _ensure_dataset_id(ctx: AgentToolContext) -> int | None:
    if (
        isinstance(ctx.dataset_id, int)
        and not isinstance(ctx.dataset_id, bool)
        and ctx.dataset_id > 0
    ):
        return ctx.dataset_id
    cached = ctx.state.get("dataset_id")
    if (
        isinstance(cached, int)
        and not isinstance(cached, bool)
        and cached > 0
    ):
        return cached
    return None


class SearchSemanticAssetsArgs(BaseModel):
    """检索语义层资产；问题与意图统一从运行状态读取。"""

    model_config = ConfigDict(extra="forbid")


class SearchSemanticAssetsResult(RootModel[dict[str, Any]]):
    """语义检索包；详细字段由 Semantic 公开契约继续收敛。"""


class SearchSemanticAssetsTool(AgentTool):
    name = "search_semantic_assets"
    description = (
        "按上游已确认的问题理解检索候选指标、维度、术语与数据表，无需传入参数。回答问数问题前必须先调用，"
        "返回的语义包（asset_id、口径、置信度、歧义提示）是后续编译 SQL 的唯一合法依据。"
    )
    args_model = SearchSemanticAssetsArgs
    result_model = SearchSemanticAssetsResult
    execution = ToolExecutionPolicy(timeout_seconds=30)

    def __init__(
        self,
        semantic_retrieval_service: SemanticAssetRetriever,
        query_service: QueryService,
    ) -> None:
        self._semantic_retrieval_service = semantic_retrieval_service
        self._query_service = query_service

    def execute(
        self,
        ctx: AgentToolContext,
        args: SearchSemanticAssetsArgs,
    ) -> ToolResult[SearchSemanticAssetsResult]:
        dataset_id = _ensure_dataset_id(ctx)
        if dataset_id is None:
            return ToolResult.failed(
                "当前数据源未绑定可用的语义数据集，无法进行语义检索。可改用 get_dataset_schema 查看物理表结构。",
                error_code="semantic_dataset_not_found",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        understanding = ctx.state.get("question_understanding")
        if not isinstance(understanding, dict):
            return ToolResult.rejected(
                "缺少已确认的问题理解，禁止在工具选择阶段重新生成检索意图。",
                error_code="question_understanding_required",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        intent = understanding.get("intent")
        question = understanding.get("rewritten_question")
        if not isinstance(intent, dict) or not isinstance(question, str) or not question.strip():
            return ToolResult.rejected(
                "已确认的问题理解结构不完整，无法执行语义检索。",
                error_code="question_understanding_invalid",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if not ctx.datasource_id or not ctx.user_id:
            return ToolResult.failed(
                "Datasource 权限服务或可信身份未配置。",
                error_code="query_policy_service_required",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        subject = DatasourceQuerySubject(
            user_id=ctx.user_id,
            workspace_id=ctx.oid,
        )
        policy = self._query_service.resolve_policy(subject, ctx.datasource_id)
        if not policy.allowed or not policy.authorized_tables:
            return ToolResult.rejected(
                policy.reason or "当前身份没有可访问的表。",
                error_code=policy.error_code or "authorized_tables_empty",
                error_category=ToolErrorCategory.AUTHORIZATION,
            )
        package = self._semantic_retrieval_service.retrieve_for_agent(
            SemanticRetrievalData(
                workspace_id=ctx.oid,
                user_id=ctx.user_id,
                dataset_id=dataset_id,
                original_question=str(ctx.state.get("question") or question),
                rewritten_question=question,
                intent=intent,
                request_id=str(ctx.state.get("run_id") or "") or None,
            )
        )
        package = self._semantic_retrieval_service.filter_authorized_tables(
            package,
            policy.authorized_tables,
        )
        # 语义包与合法资产集合入 state，供 compile 校验"只接受出现过的资产"。
        ctx.state["semantic_package"] = package
        asset_ids = set(ctx.state.get("semantic_asset_ids") or [])
        for group in (package.get("candidate_groups") or {}).values():
            asset_ids.update(item["asset_id"] for item in group if item.get("asset_id") is not None)
        for group in (package.get("selected_assets") or {}).values():
            if isinstance(group, list):
                asset_ids.update(
                    item.get("asset_id") for item in group if isinstance(item, dict) and item.get("asset_id") is not None
                )
        ctx.state["semantic_asset_ids"] = sorted(asset_ids)
        tables = set(ctx.state.get("allowed_tables") or [])
        tables.update(package.get("tables") or [])
        ctx.state["allowed_tables"] = sorted(tables)
        data = SearchSemanticAssetsResult(package)
        return ToolResult.succeeded(
            json_summary(package, _summary_limit(ctx)),
            data,
        )


class CompileFilter(BaseModel):
    asset_id: int = Field(description="过滤维度的 asset_id，必须来自语义包")
    operator: str = Field(default="=", description="过滤操作符，如 = / != / > / >= / < / <= / in / like")
    value: str | int | float | bool | dict[str, Any] = Field(
        description=(
            "过滤值。普通筛选传标量；时间筛选必须使用已确认问题理解中的 time_range.normalized，"
            "不得把 today/今天作为普通字符串，也不得替换成数据最大日期。"
        )
    )


class CompileOrderBy(BaseModel):
    biz_name: str = Field(description="排序字段的 biz_name（指标或维度）")
    direction: Literal["asc", "desc"] = "desc"


class CompileSemanticSqlArgs(BaseModel):
    """把结构化查询计划确定性编译为 SQL（口径由语义层保证）。"""

    metric_asset_ids: list[int] = Field(default_factory=list, description="指标 asset_id 列表，必须来自语义包")
    dimension_asset_ids: list[int] = Field(default_factory=list, description="分组维度 asset_id 列表，必须来自语义包")
    filters: list[CompileFilter] = Field(default_factory=list)
    time_bucket: dict[str, Any] | None = Field(
        default=None, description='时间分桶（趋势），如 {"asset_id": 12, "grain": "month"}'
    )
    order_by: list[CompileOrderBy] = Field(default_factory=list)
    limit: int | None = None


class CompileSemanticSqlResult(BaseModel):
    sql: str
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    dataset_id: int
    strategy: str


class CompileSemanticSqlTool(AgentTool):
    name = "compile_semantic_sql"
    description = (
        "把结构化查询计划确定性编译为 SQL，口径由语义层保证。这是生成 SQL 的首选方式；"
        "所有 asset_id 必须来自 search_semantic_assets 返回的语义包，否则会被拒绝。"
    )
    args_model = CompileSemanticSqlArgs
    result_model = CompileSemanticSqlResult
    execution = ToolExecutionPolicy(timeout_seconds=30)

    def __init__(self, semantic_query_service: SemanticQueryCompiler) -> None:
        self._semantic_query_service = semantic_query_service

    def execute(
        self,
        ctx: AgentToolContext,
        args: CompileSemanticSqlArgs,
    ) -> ToolResult[CompileSemanticSqlResult]:
        dataset_id = _ensure_dataset_id(ctx)
        if dataset_id is None:
            return ToolResult.failed(
                "当前数据源未绑定语义数据集，无法编译。",
                error_code="semantic_dataset_not_found",
                error_category=ToolErrorCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
            )
        known_ids = set(ctx.state.get("semantic_asset_ids") or [])
        requested = set(args.metric_asset_ids) | set(args.dimension_asset_ids) | {f.asset_id for f in args.filters}
        if args.time_bucket and isinstance(args.time_bucket.get("asset_id"), int):
            requested.add(args.time_bucket["asset_id"])
        unknown = sorted(requested - known_ids)
        if not known_ids:
            return ToolResult.rejected(
                "尚未检索语义资产，请先调用 search_semantic_assets。",
                error_code="semantic_package_required",
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        if unknown:
            return ToolResult.rejected(
                f"以下 asset_id 未出现在语义包中，禁止编造口径: {unknown}。请只使用检索结果里的资产。",
                error_code="asset_not_in_package",
                error_category=ToolErrorCategory.SAFETY,
            )
        filters = [filter_item.model_dump(mode="json") for filter_item in args.filters]
        understanding = ctx.state.get("question_understanding") or {}
        intent = understanding.get("intent") if isinstance(understanding, dict) else {}
        time_range = intent.get("time_range") if isinstance(intent, dict) else {}
        if isinstance(time_range, dict) and time_range.get("value_status") == "provided":
            normalized_time = time_range.get("normalized")
            if not isinstance(normalized_time, dict) or normalized_time.get("kind") == "unsupported":
                return ToolResult.rejected(
                    "已确认时间范围尚未归一化，禁止生成 SQL。请先澄清为系统支持的时间表达。",
                    error_code="time_range_unsupported",
                    error_category=ToolErrorCategory.BUSINESS_RULE,
                )
            matched_time_filter = False
            for filter_item in filters:
                value = filter_item["value"]
                candidate = normalize_time_range(value) if isinstance(value, str) else None
                if value == normalized_time or candidate == normalized_time:
                    filter_item["value"] = normalized_time
                    matched_time_filter = True
            if not matched_time_filter:
                return ToolResult.rejected(
                    (
                        f"时间筛选与已确认问题不一致。必须原样使用 time_range.normalized={normalized_time}，"
                        "禁止省略时间或替换成数据最大日期。"
                    ),
                    error_code="time_filter_mismatch",
                    error_category=ToolErrorCategory.SAFETY,
                )
        slots: dict[str, Any] = {
            "metrics": [{"asset_id": i, "asset_type": "METRIC"} for i in args.metric_asset_ids],
            "dimensions": [{"asset_id": i, "asset_type": "DIMENSION"} for i in args.dimension_asset_ids],
            "filters": [{**filter_item, "asset_type": "DIMENSION"} for filter_item in filters],
        }
        try:
            result = self._semantic_query_service.compile(
                SemanticQueryCompileData(
                    workspace_id=ctx.oid,
                    dataset_id=dataset_id,
                    question=str(understanding.get("rewritten_question") or ""),
                    slots=slots,
                    order_by=[item.model_dump(mode="json") for item in args.order_by],
                    limit=args.limit or getattr(ctx.config, "default_limit", 100),
                    time_bucket=args.time_bucket,
                )
            )
        except SemanticQueryCompileError as exc:
            return ToolResult.failed(
                "语义资产不足，无法使用规则编译生成 SQL",
                error_code=str(exc),
                error_category=ToolErrorCategory.DOMAIN,
                retry_advice=RetryAdvice.CORRECT_INPUT,
            )
        data = CompileSemanticSqlResult(
            sql=result.sql,
            tables=result.tables,
            metrics=result.metrics,
            dimensions=result.dimensions,
            dataset_id=result.dataset_id,
            strategy="semantic_sql_compiler",
        )
        ctx.state["compiled_sql"] = data.sql
        tables = set(ctx.state.get("allowed_tables") or [])
        tables.update(data.tables)
        ctx.state["allowed_tables"] = sorted(tables)
        return ToolResult.succeeded(
            json_summary(data.model_dump(mode="json"), _summary_limit(ctx)),
            data,
        )


class FinishArgs(BaseModel):
    """结束作答。必须已存在成功的 execute_sql 结果。"""

    answer_markdown: str = Field(min_length=1, description="面向用户的最终回答（markdown）")
    chart_type: Literal["table", "bar", "line", "pie"] | None = Field(default=None, description="推荐图表类型")
    x_field: str | None = Field(default=None, description="x 轴/类别字段名")
    y_fields: list[str] = Field(default_factory=list, description="数值系列字段名")


class FinishResult(BaseModel):
    answer: str
    chart: dict[str, Any] = Field(default_factory=dict)
    sql: str | None = None
    non_standard: bool = False


class FinishTool(AgentTool):
    name = "finish"
    description = (
        "结束本次问数并给出最终回答与图表建议。只有在 execute_sql 成功拿到真实数据后才允许调用；"
        "没有数据时应改为如实说明失败原因。"
    )
    args_model = FinishArgs
    result_model = FinishResult
    execution = ToolExecutionPolicy(timeout_seconds=5)

    def execute(
        self,
        ctx: AgentToolContext,
        args: FinishArgs,
    ) -> ToolResult[FinishResult]:
        try:
            result = project_query_final_reply(
                QueryFinalReplyProjectionData(
                    answer_markdown=args.answer_markdown,
                    execution=ctx.state.get("last_execution"),
                    chart_type=args.chart_type,
                    x_field=args.x_field,
                    y_fields=args.y_fields,
                )
            )
        except FinalReplyProjectionError as exc:
            return ToolResult.rejected(
                str(exc),
                error_code=exc.error_code,
                error_category=ToolErrorCategory.BUSINESS_RULE,
            )
        data = FinishResult.model_validate(result.model_dump(mode="json"))
        return ToolResult.succeeded(
            "finish",
            data,
        )


def build_chatbi_tools(
    *,
    query_service: QueryService,
    semantic_query_service: SemanticQueryCompiler,
    semantic_retrieval_service: SemanticAssetRetriever,
) -> list[AgentTool]:
    return [
        SearchSemanticAssetsTool(
            semantic_retrieval_service,
            query_service,
        ),
        CompileSemanticSqlTool(semantic_query_service),
        FinishTool(),
    ]
