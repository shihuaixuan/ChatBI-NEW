"""六个 P0 核心工具。全部为共享能力和语义层的薄封装，守护内嵌。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.agent.tools.base import (
    AgentTool,
    AgentToolContext,
    ToolOutput,
    json_summary,
)
from apps.capabilities.time_slots import normalize_time_range
from apps.chatbi.models import (
    ChatRecordExecutionType,
    QueryFinalReplyProjectionData,
    ResultArtifactWriteData,
    SemanticQueryCompileData,
    SemanticRetrievalData,
)
from apps.chatbi.services import (
    FinalReplyProjectionError,
    QueryService,
    ResultArtifactWriteError,
    SemanticQueryCompileError,
    project_query_final_reply,
)

SUMMARY_MAX_CHARS_DEFAULT = 4000


def _summary_limit(ctx: AgentToolContext) -> int:
    return getattr(ctx.config, "summary_max_chars", SUMMARY_MAX_CHARS_DEFAULT)


def _execution_gate(ctx: AgentToolContext) -> ToolOutput | None:
    """统一阻止未完成问题理解或仍需澄清的意图进入 SQL 阶段。"""

    understanding = ctx.state.get("question_understanding")
    if not isinstance(understanding, dict):
        return ToolOutput(
            success=False,
            summary="缺少已确认的问题理解，禁止生成或执行 SQL。",
            error_code="question_understanding_required",
        )
    validation = understanding.get("validation")
    if not isinstance(validation, dict):
        return ToolOutput(
            success=False,
            summary="问题理解缺少校验结果，禁止生成或执行 SQL。",
            error_code="question_understanding_invalid",
        )
    if validation.get("status") != "valid":
        slots = validation.get("clarification_slots") or []
        return ToolOutput(
            success=False,
            summary=f"当前问题仍需澄清槽位 {slots}，禁止生成或执行 SQL。请先调用 clarify。",
            error_code="question_clarification_required",
        )
    return None


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


class SearchSemanticAssetsTool(AgentTool):
    name = "search_semantic_assets"
    description = (
        "按上游已确认的问题理解检索候选指标、维度、术语与数据表，无需传入参数。回答问数问题前必须先调用，"
        "返回的语义包（asset_id、口径、置信度、歧义提示）是后续编译 SQL 的唯一合法依据。"
    )
    args_model = SearchSemanticAssetsArgs

    def execute(self, ctx: AgentToolContext, args: SearchSemanticAssetsArgs) -> ToolOutput:
        dataset_id = _ensure_dataset_id(ctx)
        if dataset_id is None:
            return ToolOutput(
                success=False,
                summary="当前数据源未绑定可用的语义数据集，无法进行语义检索。可改用 get_dataset_schema 查看物理表结构。",
                error_code="semantic_dataset_not_found",
            )
        understanding = ctx.state.get("question_understanding")
        if not isinstance(understanding, dict):
            return ToolOutput(
                success=False,
                summary="缺少已确认的问题理解，禁止在工具选择阶段重新生成检索意图。",
                error_code="question_understanding_required",
            )
        intent = understanding.get("intent")
        question = understanding.get("rewritten_question")
        if not isinstance(intent, dict) or not isinstance(question, str) or not question.strip():
            return ToolOutput(
                success=False,
                summary="已确认的问题理解结构不完整，无法执行语义检索。",
                error_code="question_understanding_invalid",
            )
        if ctx.semantic_retrieval_service is None:
            return ToolOutput(
                success=False,
                summary="ChatBI 语义检索服务未配置。",
                error_code="semantic_retrieval_service_required",
            )
        package = ctx.semantic_retrieval_service.retrieve_for_agent(
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
        return ToolOutput(success=True, summary=json_summary(package, _summary_limit(ctx)), payload=package)


class GetDatasetSchemaArgs(BaseModel):
    """查看当前数据源的物理表结构。"""

    table_keyword: str = Field(default="", description="可选，按表名/注释过滤")


class GetDatasetSchemaTool(AgentTool):
    name = "get_dataset_schema"
    description = (
        "查看当前数据源的物理表、字段、类型与注释。语义检索未覆盖（无指标定义的明细查询）时，"
        "基于此结构手写 SQL；手写 SQL 属于非标准口径，结果会附带口径提示。"
    )
    args_model = GetDatasetSchemaArgs

    def execute(self, ctx: AgentToolContext, args: GetDatasetSchemaArgs) -> ToolOutput:
        if not ctx.datasource_id:
            return ToolOutput(success=False, summary="缺少数据源，无法查看表结构。", error_code="datasource_required")
        if ctx.physical_schema_service is None:
            return ToolOutput(
                success=False,
                summary="ChatBI 物理 Schema 服务未配置。",
                error_code="physical_schema_service_required",
            )
        schema = ctx.physical_schema_service.get(
            ctx.datasource_id,
            table_keyword=args.table_keyword,
        )
        items = [
            {
                "table": table.name,
                "comment": table.comment,
                "fields": [
                    {
                        "name": field.name,
                        "type": field.data_type,
                        "comment": field.comment,
                    }
                    for field in table.fields
                ],
            }
            for table in schema.tables
        ]
        allowed = set(ctx.state.get("allowed_tables") or [])
        allowed.update(item["table"] for item in items)
        ctx.state["allowed_tables"] = sorted(allowed)
        payload = {"tables": items, "table_count": len(items)}
        return ToolOutput(
            success=True,
            summary=json_summary(payload, _summary_limit(ctx)),
            payload=payload,
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


class CompileSemanticSqlTool(AgentTool):
    name = "compile_semantic_sql"
    description = (
        "把结构化查询计划确定性编译为 SQL，口径由语义层保证。这是生成 SQL 的首选方式；"
        "所有 asset_id 必须来自 search_semantic_assets 返回的语义包，否则会被拒绝。"
    )
    args_model = CompileSemanticSqlArgs

    def execute(self, ctx: AgentToolContext, args: CompileSemanticSqlArgs) -> ToolOutput:
        blocked = _execution_gate(ctx)
        if blocked:
            return blocked
        dataset_id = _ensure_dataset_id(ctx)
        if dataset_id is None:
            return ToolOutput(success=False, summary="当前数据源未绑定语义数据集，无法编译。", error_code="semantic_dataset_not_found")
        known_ids = set(ctx.state.get("semantic_asset_ids") or [])
        requested = set(args.metric_asset_ids) | set(args.dimension_asset_ids) | {f.asset_id for f in args.filters}
        if args.time_bucket and isinstance(args.time_bucket.get("asset_id"), int):
            requested.add(args.time_bucket["asset_id"])
        unknown = sorted(requested - known_ids)
        if not known_ids:
            return ToolOutput(
                success=False,
                summary="尚未检索语义资产，请先调用 search_semantic_assets。",
                error_code="semantic_package_required",
            )
        if unknown:
            return ToolOutput(
                success=False,
                summary=f"以下 asset_id 未出现在语义包中，禁止编造口径: {unknown}。请只使用检索结果里的资产。",
                error_code="asset_not_in_package",
            )
        filters = [filter_item.model_dump(mode="json") for filter_item in args.filters]
        understanding = ctx.state.get("question_understanding") or {}
        intent = understanding.get("intent") if isinstance(understanding, dict) else {}
        time_range = intent.get("time_range") if isinstance(intent, dict) else {}
        if isinstance(time_range, dict) and time_range.get("value_status") == "provided":
            normalized_time = time_range.get("normalized")
            if not isinstance(normalized_time, dict) or normalized_time.get("kind") == "unsupported":
                return ToolOutput(
                    success=False,
                    summary="已确认时间范围尚未归一化，禁止生成 SQL。请先澄清为系统支持的时间表达。",
                    error_code="time_range_unsupported",
                )
            matched_time_filter = False
            for filter_item in filters:
                value = filter_item["value"]
                candidate = normalize_time_range(value) if isinstance(value, str) else None
                if value == normalized_time or candidate == normalized_time:
                    filter_item["value"] = normalized_time
                    matched_time_filter = True
            if not matched_time_filter:
                return ToolOutput(
                    success=False,
                    summary=(
                        f"时间筛选与已确认问题不一致。必须原样使用 time_range.normalized={normalized_time}，"
                        "禁止省略时间或替换成数据最大日期。"
                    ),
                    error_code="time_filter_mismatch",
                )
        slots: dict[str, Any] = {
            "metrics": [{"asset_id": i, "asset_type": "METRIC"} for i in args.metric_asset_ids],
            "dimensions": [{"asset_id": i, "asset_type": "DIMENSION"} for i in args.dimension_asset_ids],
            "filters": [{**filter_item, "asset_type": "DIMENSION"} for filter_item in filters],
        }
        if ctx.semantic_query_service is None:
            return ToolOutput(
                success=False,
                summary="ChatBI 语义查询服务未配置。",
                error_code="semantic_query_service_required",
            )
        try:
            result = ctx.semantic_query_service.compile(
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
            return ToolOutput(
                success=False,
                summary="语义资产不足，无法使用规则编译生成 SQL",
                error_code=str(exc),
            )
        payload = {
            "sql": result.sql,
            "tables": result.tables,
            "metrics": result.metrics,
            "dimensions": result.dimensions,
            "dataset_id": result.dataset_id,
            "strategy": "semantic_sql_compiler",
        }
        ctx.state["compiled_sql"] = payload["sql"]
        tables = set(ctx.state.get("allowed_tables") or [])
        tables.update(payload.get("tables") or [])
        ctx.state["allowed_tables"] = sorted(tables)
        return ToolOutput(success=True, summary=json_summary(payload, _summary_limit(ctx)), payload=payload)


class ValidateSqlArgs(BaseModel):
    sql: str = Field(min_length=1, description="待校验的 SQL")


class ValidateSqlTool(AgentTool):
    name = "validate_sql"
    description = (
        "校验 SQL：单语句、只读（SELECT/WITH）、无危险关键字、表在白名单内，并自动补 LIMIT。"
        "手写 SQL 在执行前必须先通过校验。"
    )
    args_model = ValidateSqlArgs

    def execute(self, ctx: AgentToolContext, args: ValidateSqlArgs) -> ToolOutput:
        blocked = _execution_gate(ctx)
        if blocked:
            return blocked
        service = ctx.query_service or QueryService(
            default_limit=getattr(ctx.config, "default_limit", 100),
        )
        result = service.validate_sql(
            args.sql,
            allowed_tables=ctx.state.get("allowed_tables") or [],
        )
        if not result.success:
            return ToolOutput(success=False, summary=result.message or "SQL 校验失败", error_code=result.error_code)
        return ToolOutput(success=True, summary=json_summary(result.payload, _summary_limit(ctx)), payload=result.payload)


class ExecuteSqlArgs(BaseModel):
    sql: str = Field(min_length=1, description="要执行的 SQL；优先使用 compile_semantic_sql 的产出")


class ExecuteSqlTool(AgentTool):
    name = "execute_sql"
    description = (
        "执行只读 SQL 并返回样本行与统计摘要。内部强制：权限改写 → 只读校验 → 执行 → 截断。"
        "完整结果自动存档，不要试图获取全量数据。"
    )
    args_model = ExecuteSqlArgs

    def execute(self, ctx: AgentToolContext, args: ExecuteSqlArgs) -> ToolOutput:
        blocked = _execution_gate(ctx)
        if blocked:
            return blocked
        if not ctx.datasource_id:
            return ToolOutput(success=False, summary="缺少数据源，无法执行。", error_code="datasource_required")
        if ctx.query_service is None:
            return ToolOutput(
                success=False,
                summary="ChatBI 查询服务未配置。",
                error_code="query_service_required",
            )
        if (
            ctx.result_artifact_service is None
            or not ctx.execution_id
            or ctx.chat_id is None
            or ctx.record_id is None
        ):
            return ToolOutput(
                success=False,
                summary="ChatBI 结果 Artifact 服务或执行归属未配置。",
                error_code="result_artifact_service_required",
            )
        result = ctx.query_service.execute_sql(
            sql=args.sql,
            datasource_id=ctx.datasource_id,
            workspace_id=ctx.oid,
            user_id=ctx.user_id,
            allowed_tables=ctx.state.get("allowed_tables") or [],
        )
        if not result.success:
            return ToolOutput(success=False, summary=result.message or (result.error_code or "执行失败"), error_code=result.error_code)
        payload = dict(result.payload or {})
        full_data = payload.pop("full_data", [])
        try:
            artifact_ref = ctx.result_artifact_service.save(
                ResultArtifactWriteData(
                    execution_id=ctx.execution_id,
                    execution_type=ChatRecordExecutionType.AGENT,
                    chat_id=ctx.chat_id,
                    record_id=ctx.record_id,
                    kind="sql_result",
                    payload={
                        "query_id": "query-0",
                        "fields": payload["fields"],
                        "rows": full_data,
                        "row_count": payload["row_count"],
                    },
                    metadata={
                        "query_id": "query-0",
                        "row_count": payload["row_count"],
                    },
                )
            )
        except ResultArtifactWriteError:
            return ToolOutput(
                success=False,
                summary="SQL 结果 Artifact 写入失败。",
                error_code="sql_result_artifact_write_failed",
            )
        artifact_ref_payload = artifact_ref.model_dump(mode="json")
        payload["artifact_ref"] = artifact_ref_payload
        compiled = ctx.state.get("compiled_sql")
        sql_source = "compiled" if compiled and _normalize(args.sql) == _normalize(compiled) else "manual"
        ctx.state["last_execution"] = {
            "sql": payload["sql"],
            "fields": payload["fields"],
            "row_count": payload["row_count"],
            "sample_rows": payload["sample_rows"],
            "artifact_ref": artifact_ref_payload,
            "sql_source": sql_source,
        }
        ctx.state["full_data"] = full_data
        summary_view = {key: payload[key] for key in ("sql", "fields", "sample_rows", "row_count", "stats_summary")}
        summary_view["sql_source"] = sql_source
        return ToolOutput(success=True, summary=json_summary(summary_view, _summary_limit(ctx)), payload={**payload, "sql_source": sql_source})


class FinishArgs(BaseModel):
    """结束作答。必须已存在成功的 execute_sql 结果。"""

    answer_markdown: str = Field(min_length=1, description="面向用户的最终回答（markdown）")
    chart_type: Literal["table", "bar", "line", "pie"] | None = Field(default=None, description="推荐图表类型")
    x_field: str | None = Field(default=None, description="x 轴/类别字段名")
    y_fields: list[str] = Field(default_factory=list, description="数值系列字段名")


class FinishTool(AgentTool):
    name = "finish"
    description = (
        "结束本次问数并给出最终回答与图表建议。只有在 execute_sql 成功拿到真实数据后才允许调用；"
        "没有数据时应改为如实说明失败原因。"
    )
    args_model = FinishArgs

    def execute(self, ctx: AgentToolContext, args: FinishArgs) -> ToolOutput:
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
            return ToolOutput(
                success=False,
                summary=str(exc),
                error_code=exc.error_code,
            )
        return ToolOutput(
            success=True,
            summary="finish",
            payload=result.model_dump(mode="json"),
        )


def _normalize(sql: str) -> str:
    return " ".join((sql or "").lower().split()).rstrip(";")


def build_default_tools() -> list[AgentTool]:
    return [
        SearchSemanticAssetsTool(),
        GetDatasetSchemaTool(),
        CompileSemanticSqlTool(),
        ValidateSqlTool(),
        ExecuteSqlTool(),
        FinishTool(),
    ]
