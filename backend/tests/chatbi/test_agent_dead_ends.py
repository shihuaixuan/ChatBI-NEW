"""P0-3 死角修补回归：理解校验失败、STRICT 非 PROVEN、finish 失败都不再死循环。"""

from __future__ import annotations

from threading import Lock
from time import sleep
from types import SimpleNamespace
from typing import Any

import pytest

from apps.chatbi.errors import AgentFinalizationError, QuestionUnderstandingError
from apps.chatbi.models import AgentErrorClass
from apps.chatbi.models.dto.analysis_plan import (
    AnalysisPlan,
    CompiledQuery,
    ComputeOperation,
    ComputeTask,
    PlanEdge,
    PresentationHint,
    QueryTask,
    QueryTaskSpec,
)
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.preparation import AgentInputPreparer
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_execution import _bounded_summary
from apps.chatbi.orchestration.agent.tool_visibility import visible_tool_names
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.orchestration.agent.tools.core import FinishTool
from apps.chatbi.orchestration.pipeline.plan_mode import PlanPipeline, PlanPipelineError
from apps.chatbi.services.execution import (
    QueryTaskExecutionRequest,
    QueryTaskExecutionResult,
    QueryTaskExecutionStatus,
    QueryTaskExecutor,
)
from apps.chatbi.services.generation.agent_finalization import (
    AgentFinalizationInput,
    AgentFinalizationResult,
    build_partial_finalization,
)
from apps.chatbi.services.planning.analysis_planner import AnalysisPlanner
from apps.chatbi.services.planning.dag_scheduler import build_execution_batches
from apps.datasource.models.dto.query import (
    DatasourceQueryData,
    DatasourceQueryResult,
)
from apps.tool import BudgetGuard, NeverCancelled, ToolStatus
from apps.tool.context import current_tool_call_context

REGISTERED_TOOLS = [
    "parse_time_range",
    "search_semantic_assets",
    "search_terminology",
    "get_sql_examples",
    "get_dataset_schema",
    "compile_semantic_sql",
    "validate_sql",
    "execute_sql",
    "clarify",
    "finish",
]


def _state() -> AgentRuntimeState:
    state = AgentRuntimeState(
        run=type("Run", (), {"id": 1, "oid": 1, "chat_id": 1})(),
        record=type("Record", (), {"id": 1})(),
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=1),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )
    state.context.state["question_understanding"] = {
        "validation": {"status": "valid"},
        "category": "data_query",
    }
    return state


def test_invalid_validation_exposes_clarify_instead_of_nothing():
    state = _state()
    state.context.state["question_understanding"] = {
        "validation": {
            "status": "clarification_required",
            "reason_codes": ["intent_unknown"],
        },
        "category": "data_query",
    }
    assert visible_tool_names(state, "normal", REGISTERED_TOOLS) == ["clarify"]


def test_strict_unproven_plan_exposes_clarify_instead_of_nothing():
    state = _state()
    state.context.state["semantic_scope"] = {
        "semantic_enforcement": "STRICT",
        "decision_status": "resolved",
        "query_plan": {"validation_status": "NOT_PROVEN"},
        "validation_report": {"status": "NOT_PROVEN"},
    }
    assert visible_tool_names(state, "normal", REGISTERED_TOOLS) == ["clarify"]


def test_parse_time_range_revisible_when_scope_lacks_time():
    state = _state()
    state.context.state["semantic_scope"] = {
        "decision_status": "degraded",
    }
    visible = visible_tool_names(state, "normal", REGISTERED_TOOLS)
    assert visible[0] == "parse_time_range"

    # 时间已解析后不再重复暴露时间工具。
    state.context.state["time_range"] = {"kind": "absolute"}
    visible = visible_tool_names(state, "normal", REGISTERED_TOOLS)
    assert "parse_time_range" not in visible


class _FailingFinalization:
    def generate(self, data: AgentFinalizationInput) -> AgentFinalizationResult:
        raise AgentFinalizationError(
            "AGENT_CHART_FIELD_INVALID",
            "图表配置使用了查询结果之外的字段。",
        )


class _Ctx:
    """FinishTool 所需的最小执行上下文。"""

    def __init__(self, state: dict[str, Any]):
        self.state = state
        self.oid = 1
        self.user_id = 1


def test_finish_degrades_to_partial_answer_on_finalization_error():
    tool = FinishTool(_FailingFinalization())
    state = {
        "question": "本月销售额",
        "question_understanding": {"intent": {"intent_type": "metric_query"}},
        "last_execution": {
            "sql": "select 1",
            "fields": ["region", "sales"],
            "row_count": 2,
            "status": "succeeded",
        },
        "full_data": [
            {"region": "华东", "sales": 100},
            {"region": "华北", "sales": 80},
        ],
    }
    result = tool.execute(_Ctx(state), args=type("Args", (), {})())
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data is not None
    assert result.data.chart == {}
    assert result.data.sql == "select 1"
    assert "AGENT_CHART_FIELD_INVALID" in result.data.answer
    assert "华东" in result.data.answer


def test_build_partial_finalization_keeps_real_numbers_only():
    partial = build_partial_finalization(
        execution={"sql": "select 1", "fields": ["region", "sales"], "row_count": 2},
        rows=[{"region": "华东", "sales": 100}, {"region": "华北", "sales": 80}],
        failed_stage="agent_answer_generation",
    )
    assert partial.chart == {}
    assert "行数：2" in partial.answer
    assert "region、sales" in partial.answer
    assert "华东" in partial.answer and "华北" in partial.answer


def test_bounded_summary_keeps_parseable_structure_when_truncating():
    rows = [{"region": f"区域{i}", "sales": i, "extra": "x" * 400} for i in range(50)]
    payload = {
        "fields": ["region", "sales", "extra"],
        "rows": rows,
        "row_count": 50,
    }
    summary = _bounded_summary(payload)
    import orjson

    encoded = orjson.dumps(summary).decode()
    assert len(encoded) <= 2000
    # 结构仍然可解析，schema 与统计保留，而不是半截 JSON 字符串。
    assert summary["fields"] == ["region", "sales", "extra"]
    assert summary["row_count"] == 50
    assert isinstance(summary["rows"], list) and summary["rows"]
    assert summary["_truncated"] is True


def test_bounded_summary_unchanged_when_small():
    payload = {"fields": ["a"], "row_count": 1}
    assert _bounded_summary(payload) == payload


def test_agent_error_class_extended_for_failure_attribution():
    assert AgentErrorClass.BINDING_AMBIGUOUS.value == "binding_ambiguous"
    assert AgentErrorClass.PLAN_INVALID.value == "plan_invalid"
    assert AgentErrorClass.FINALIZE.value == "finalize_failed"


def test_plan_proves_all_queries_before_any_execution() -> None:
    """后续查询证明失败时，前面的查询也不能提前执行。"""

    tool_calls: list[str] = []
    saved_plans = []
    pipeline = object.__new__(PlanPipeline)
    pipeline._metrics = None
    pipeline._planner = AnalysisPlanner(max_query_tasks=5)
    pipeline._max_query_tasks = 5
    pipeline._session = SimpleNamespace(commit=lambda: None)
    pipeline._events = SimpleNamespace(
        plan_created=lambda *args: "plan-created",
        plan_updated=lambda *args: "plan-updated",
    )
    pipeline._save_plan = lambda _state, plan: saved_plans.append(plan)
    compile_count = 0

    def compile_task(_state, _task, *, strict_query_index=0):
        nonlocal compile_count
        compile_count += 1
        if compile_count == 2:
            raise PlanPipelineError("PLAN_SECOND_QUERY_COMPILE_FAILED")
        return CompiledQuery(
            plan_fingerprint=f"proof:{strict_query_index}",
            sql="SELECT 1",
        )

    def call_tool(_state, name, _args):
        tool_calls.append(name)
        return SimpleNamespace(data=None)

    pipeline._compile_task = compile_task
    pipeline._call_tool = call_tool
    state = SimpleNamespace(
        record=SimpleNamespace(id=1, question="销售额和欠款"),
        context=SimpleNamespace(
            dataset_id=1,
            semantic_asset_scope=None,
            state={
                "execution_requirement": {
                    "status": "ready",
                    "route": {"mode": "plan"},
                    "query_requirements": [
                        {
                            "id": "sales",
                            "model_ref": "MODEL:10",
                            "metrics": [{"ref": "METRIC:1:10", "asset_id": 1}],
                        },
                        {
                            "id": "arrears",
                            "model_ref": "MODEL:11",
                            "metrics": [{"ref": "METRIC:2:11", "asset_id": 2}],
                        },
                    ],
                    "post_calculations": [
                        {
                            "id": "merge_results",
                            "type": "merge",
                            "inputs": ["sales", "arrears"],
                        }
                    ],
                    "runtime": {"dataset_id": 1},
                },
                "question_understanding": {},
            },
        ),
        require_run_id=lambda: 100,
    )

    with pytest.raises(PlanPipelineError, match="PLAN_SECOND_QUERY_COMPILE_FAILED"):
        list(pipeline.run(state))

    assert tool_calls == ["validate_sql"]
    assert len(saved_plans) == 1
    assert saved_plans[0].validation.status.value == "DRAFT"


def test_plan_dag_builds_deterministic_parallel_batches() -> None:
    """无依赖查询同批执行，计算节点只能进入后续依赖已完成的批次。"""

    plan = AnalysisPlan(
        id="plan-1",
        tasks=(
            QueryTask(id="current", spec=QueryTaskSpec(dataset_id=1)),
            QueryTask(id="previous", spec=QueryTaskSpec(dataset_id=1)),
            ComputeTask(
                id="growth",
                operation=ComputeOperation.GROWTH_RATE,
                inputs=("current", "previous"),
            ),
            ComputeTask(
                id="share",
                operation=ComputeOperation.SHARE,
                inputs=("growth",),
            ),
        ),
        edges=(
            PlanEdge(source="current", target="growth"),
            PlanEdge(source="previous", target="growth"),
            PlanEdge(source="growth", target="share"),
        ),
        presentation=PresentationHint(primary_result="share"),
    )

    assert build_execution_batches(plan) == (
        ("current", "previous"),
        ("growth",),
        ("share",),
    )


def test_plan_query_batch_executes_tasks_in_parallel() -> None:
    """同一拓扑批次的 QueryTask 必须真正并行，而不是仅分组后顺序执行。"""

    class RecordingExecutor:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = Lock()

        def execute(self, request):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            sleep(0.05)
            with self.lock:
                self.active -= 1
            return QueryTaskExecutionResult(
                task_id=request.task_id,
                attempt=request.attempt,
                status=QueryTaskExecutionStatus.FAILED,
                error_code="expected_test_failure",
            )

    executor = RecordingExecutor()
    pipeline = object.__new__(PlanPipeline)
    pipeline._query_task_executor = executor
    pipeline._query_concurrency = 2
    pipeline._query_timeout_seconds = 10.0
    state = SimpleNamespace(
        context=SimpleNamespace(datasource_id=1, oid=1, user_id=1),
        cancellation=NeverCancelled(),
        budget=BudgetGuard(timeout_seconds=10),
    )
    tasks = [
        QueryTask(
            id=task_id,
            spec=QueryTaskSpec(dataset_id=1),
            compiled=CompiledQuery(
                plan_fingerprint=f"proof:{task_id}",
                sql="SELECT 1",
                tables=("orders",),
            ),
        )
        for task_id in ("query-a", "query-b")
    ]
    states = {
        task.id: {"status": "RUNNING", "attempt": 1, "error_code": None}
        for task in tasks
    }

    results = pipeline._run_query_batch(state, tasks, states)

    assert set(results) == {"query-a", "query-b"}
    assert executor.max_active == 2


def test_query_task_executor_uses_independent_session_and_call_context() -> None:
    """每个 QueryTask 都创建并关闭自己的 Session，同时绑定独立调用上下文。"""

    sessions = []
    call_ids = []

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

    class QueryService:
        def execute(self, request):
            context = current_tool_call_context()
            assert context is not None
            call_ids.append(context.tool_call_id)
            return DatasourceQueryResult.succeeded(
                DatasourceQueryData(
                    sql=request.sql,
                    fields=["value"],
                    sample_rows=[{"value": 1}],
                    full_data=[{"value": 1}],
                    row_count=1,
                )
            )

    def session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    executor = QueryTaskExecutor(session_factory, lambda _session: QueryService())
    for task_id in ("query-a", "query-b"):
        result = executor.execute(
            QueryTaskExecutionRequest(
                task_id=task_id,
                attempt=1,
                sql="SELECT 1",
                datasource_id=1,
                workspace_id=1,
                user_id=1,
                selected_tables=("orders",),
                deadline_monotonic=None,
                cancellation=NeverCancelled(),
            )
        )
        assert result.status is QueryTaskExecutionStatus.SUCCEEDED

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert all(session.closed for session in sessions)
    assert call_ids == ["plan-query:query-a:1", "plan-query:query-b:1"]


def test_plan_failed_dependency_is_skipped_after_parallel_batch() -> None:
    """一个查询失败后，无依赖的同批查询仍成功，下游计算明确标记为跳过。"""

    plan = AnalysisPlan(
        id="plan-failure",
        tasks=(
            QueryTask(id="query-a", spec=QueryTaskSpec(dataset_id=1)),
            QueryTask(id="query-b", spec=QueryTaskSpec(dataset_id=1)),
            ComputeTask(
                id="merge",
                operation=ComputeOperation.MERGE,
                inputs=("query-a", "query-b"),
            ),
        ),
        edges=(
            PlanEdge(source="query-a", target="merge"),
            PlanEdge(source="query-b", target="merge"),
        ),
        presentation=PresentationHint(primary_result="merge"),
    )
    pipeline = object.__new__(PlanPipeline)
    pipeline._query_task_executor = object()
    pipeline._events = SimpleNamespace(
        task_started=lambda *_args: "task-started",
        task_finished=lambda *_args: "task-finished",
        compute_finished=lambda *_args: "compute-finished",
    )
    pipeline._persist_state = lambda _state: None
    pipeline._run_query_batch = lambda *_args: {
        "query-a": QueryTaskExecutionResult(
            task_id="query-a",
            attempt=1,
            status=QueryTaskExecutionStatus.FAILED,
            error_code="query_failed",
        ),
        "query-b": QueryTaskExecutionResult(
            task_id="query-b",
            attempt=1,
            status=QueryTaskExecutionStatus.SUCCEEDED,
            data=DatasourceQueryData(sql="SELECT 1"),
        ),
    }
    pipeline._register_query_result = lambda *_args: (
        {"result_set_id": "result:plan-failure:query-b"},
        [],
    )
    def run_compute_batch(_state, tasks):
        if tasks:
            pytest.fail("依赖失败的 ComputeTask 不应执行")
        return {}

    pipeline._run_compute_batch = run_compute_batch
    state = SimpleNamespace(
        context=SimpleNamespace(state={}),
        record=SimpleNamespace(id=1),
        cancellation=NeverCancelled(),
        require_run_id=lambda: 1,
    )

    with pytest.raises(PlanPipelineError, match="PLAN_DEPENDENCY_FAILED"):
        list(pipeline._execute_plan_batches(state, plan, {}, {}))

    task_states = state.context.state["plan_task_states"]
    assert task_states["query-a"]["status"] == "FAILED"
    assert task_states["query-b"]["status"] == "SUCCEEDED"
    assert task_states["merge"]["status"] == "SKIPPED_DEPENDENCY"


def test_semantic_clarification_resume_only_reparses_candidates() -> None:
    """澄清恢复只重跑语义解析，不重新执行问题重写和候选检索。"""

    parse_calls = []
    preparer = object.__new__(AgentInputPreparer)

    def parse(*, rewrite_question, candidate_payload):
        parse_calls.append((rewrite_question, candidate_payload))
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "status": "resolved",
                "measures": [{"ref": "METRIC:1:10"}],
            }
        )

    preparer._semantic_parse_service = SimpleNamespace(parse=parse)
    persisted = []
    preparer._persist_snapshot = lambda state: persisted.append(state)
    candidate_groups = {"metrics": [{"ref": "METRIC:1:10"}]}
    state = SimpleNamespace(
        context=SimpleNamespace(
            state={
                "question_rewrite": {"rewrite_question": "查询客户数"},
                "candidate_groups": candidate_groups,
            }
        ),
        messages=[],
    )
    clarification = SimpleNamespace(
        resume_kind="question_understanding",
        resume_payload={"operation": "resume_semantic_parse"},
    )

    assert list(preparer.prepare_resume(state, clarification, "销售客户数")) == []
    assert parse_calls == [
        (
            "查询客户数\n销售客户数",
            {"candidate_groups": candidate_groups},
        )
    ]
    assert state.context.state["semantic_parse"]["status"] == "resolved"
    assert len(state.messages) == 1
    assert persisted == [state]


def test_question_rewrite_repair_receives_specific_validation_error() -> None:
    """确定性模型重试必须知道具体契约错误，不能重复同一份无效输出。"""

    prompts: list[str] = []
    payloads = [
        {
            "original_question": "分析渠道贡献",
            "rewrite_question": "分析渠道贡献",
            "metric_phrases": ["渠道下降贡献"],
            "dimension_phrases": ["渠道"],
        },
        {
            "original_question": "分析渠道贡献",
            "rewrite_question": "分析渠道贡献",
            "metric_phrases": [],
            "dimension_phrases": ["渠道"],
        },
    ]

    def invoke(data):
        prompts.append(data.user_prompt)
        return SimpleNamespace(
            payload=payloads.pop(0),
            usage_metadata={},
        )

    preparer = object.__new__(AgentInputPreparer)
    preparer._rewrite_model_service = SimpleNamespace(invoke=invoke)
    preparer._semantic_parse_service = object()
    preparer._search_tool = SimpleNamespace(
        execute=lambda *_args, **_kwargs: SimpleNamespace(
            status=ToolStatus.FAILED,
            data=None,
            error_code="STOP_AFTER_REWRITE",
        )
    )
    preparer._conversation_context = lambda _state: {}
    preparer._node_logger = None
    state = SimpleNamespace(
        record=SimpleNamespace(question="分析渠道贡献"),
        context=SimpleNamespace(
            user_id=1,
            dataset_id=1,
            oid=1,
            state={},
        ),
        temporal_context=SimpleNamespace(
            reference_at=SimpleNamespace(isoformat=lambda: "2026-08-21T00:00:00+08:00"),
            timezone="Asia/Shanghai",
        ),
        budget=SimpleNamespace(record_llm_usage=lambda _usage: None),
        require_run_id=lambda: 1,
    )

    with pytest.raises(QuestionUnderstandingError, match="STOP_AFTER_REWRITE"):
        list(preparer.prepare_initial(state))

    assert len(prompts) == 2
    assert "检索短语必须来自 rewrite_question" in prompts[1]
