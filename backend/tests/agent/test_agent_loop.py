"""AgentLoop 端到端行为测试：FakeSession + 脚本化模型客户端，不依赖真实 DB/LLM。"""

from contextlib import contextmanager
from types import SimpleNamespace

import orjson
import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from apps.chatbi.errors import QuestionUnderstandingError
from apps.chatbi.models import (
    AgentConfig,
    AgentRunStatus,
    ChatbiAgentRun,
    ChatbiAgentStep,
    ChatbiAgentToolCall,
    EventLog,
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionUnderstandingOutcome,
    QuestionUnderstandingOutput,
)
from apps.chatbi.orchestration.agent.composition import build_agent_loop
from apps.chatbi.orchestration.agent.messages import AgentMessage, ModelDecision
from apps.chatbi.orchestration.agent.reasoning import AgentReasoner
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tool_visibility import visible_tool_names
from apps.chatbi.orchestration.agent.tools.base import AgentTool, AgentToolContext
from apps.chatbi.repository.sqlmodel import agent_run_repository
from apps.chatbi.services.understanding import (
    QuestionUnderstandingModelResponse,
    QuestionUnderstandingService,
)
from apps.conversation.models import ChatRecord
from apps.event import (
    EventPublisher,
    create_render_event,
    encode_sse_event,
)
from apps.event import list_events_after as list_persisted_events_after
from apps.tool import (
    BudgetGuard,
    RetryAdvice,
    ToolCall,
    ToolErrorCategory,
    ToolRegistry,
    ToolResult,
)
from apps.trace import DisabledAgentTracer, TraceConfig
from apps.trace.setup import (
    OpenTelemetryAgentTracer,
    ResilientAgentTracer,
    build_agent_tracer,
)


class FakeSession:
    def __init__(self):
        self.trace_count = 0
        self.added = []
        self.commit_count = 0
        self.tool_event_commits = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, EventLog):
            self.trace_count += 1

    def commit(self):
        self.commit_count += 1
        if not self.added or not isinstance(self.added[-1], EventLog):
            return
        event = self.added[-1]
        if event.event_type not in {"tool-called", "tool-result", "tool-failed"}:
            return
        tool_call_id = (event.payload or {}).get("tool_call_id")
        row = next(
            (
                item
                for item in reversed(self.added[:-1])
                if isinstance(item, ChatbiAgentToolCall)
                and item.tool_call_id == tool_call_id
            ),
            None,
        )
        self.tool_event_commits.append(
            (event.event_type, tool_call_id, getattr(row, "status", None))
        )

    def flush(self):
        pass

    def refresh(self, obj):
        pass

    def exec(self, stmt):
        count = self.trace_count
        return SimpleNamespace(
            all=lambda: [],
            one=lambda: count,
            scalar=lambda: count,
            scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None),
        )


def test_event_app_exports_structured_contract():
    assert agent_run_repository.list_events_after is list_persisted_events_after
    assert EventLog.__tablename__ == "chatbi_agent_event"

    frame = encode_sse_event(
        create_render_event(
            "answer",
            content={"content": "完成"},
            record_id=7,
            run_id=9,
            sequence=3,
        )
    )
    assert orjson.loads(frame.removeprefix("data:").strip()) == {
        "kind": "text",
        "phase": "end",
        "domain": "answer.completed",
        "content": {"content": "完成"},
        "record_id": 7,
        "run_id": 9,
        "sequence": 3,
        "block_id": "text:9",
    }


def test_event_publisher_assigns_sequence_without_committing_application_state():
    session = FakeSession()
    publisher = EventPublisher(session)

    first = publisher.publish(9, "run-started", {"record_id": 7})
    second = publisher.publish(9, "answer", {"content": "完成"})

    assert [first.sequence, second.sequence] == [1, 2]
    assert (first.kind, first.phase, first.domain) == ("run", "start", "run.started")
    assert (second.kind, second.phase, second.domain) == ("text", "end", "answer.completed")
    assert session.added[0].payload["kind"] == "run"
    assert session.added[1].payload["domain"] == "answer.completed"
    assert session.commit_count == 0


def test_render_event_contract_adds_stable_domain_and_block_id():
    event = create_render_event(
        "tool-called",
        {
            "record_id": 7,
            "tool_call_id": "call-1",
            "tool_name": "execute_sql",
        },
        record_id=7,
        run_id=9,
        sequence=4,
        step_id=3,
    )

    assert (event.kind, event.phase, event.domain) == ("tool", "start", "tool.called")
    assert event.block_id == "tool:9:call-1"


class ScriptedModel:
    """按脚本依次返回 AIMessage。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.tool_definition_calls = []

    def invoke(self, messages, tool_definitions):
        self.calls.append(messages)
        self.tool_definition_calls.append(tool_definitions)
        response = self.responses.pop(0)
        if isinstance(response, ModelDecision):
            return response
        tool_calls = [
            ToolCall(
                name=str(call.get("name") or ""),
                args=dict(call.get("args") or {}),
                call_id=str(call.get("id") or ""),
            )
            for call in response.tool_calls
        ]
        usage = dict(response.usage_metadata or {})
        message = AgentMessage.assistant(
            str(response.content or ""),
            tool_calls=tool_calls,
            reasoning_content=str(
                response.additional_kwargs.get("reasoning_content") or ""
            )
            or None,
            usage=usage,
        )
        return ModelDecision(message=message, tool_calls=tool_calls, usage=usage)


class RecordingTracer:
    """记录 span 层级和关闭状态的测试 tracer。"""

    def __init__(self):
        self.active = []
        self.spans = []

    @contextmanager
    def span(self, name, attributes=None):
        item = SimpleNamespace(
            name=name,
            parent=self.active[-1].name if self.active else None,
            attributes=dict(attributes or {}),
            closed=False,
            set_attribute=lambda key, value: item.attributes.__setitem__(key, value),
        )
        self.spans.append(item)
        self.active.append(item)
        try:
            yield item
        finally:
            self.active.pop()
            item.closed = True


class FailingTracer:
    def span(self, name, attributes=None):
        class FailingManager:
            def __enter__(self):
                raise RuntimeError("exporter unavailable")

            def __exit__(self, exc_type, exc, tb):
                return False

        return FailingManager()


class StaticUnderstandingService:
    """AgentLoop 测试使用的确定性问题理解结果。"""

    def __init__(self, rewritten_question="按城市看 gmv"):
        self.rewritten_question = rewritten_question

    def understand(self, *, question, datasource_id, conversation_context=None):
        return QuestionUnderstandingOutcome(
            output=QuestionUnderstandingOutput(
                original_question=question,
                message_type="new_question",
                rewritten_question=self.rewritten_question,
                inherited_context={},
                intent=IntentRecognitionOutput(
                    intent_type="metric_query",
                    confidence=0.95,
                    metric_mentions=["gmv"],
                    dimension_mentions=["城市"],
                    dimension_slots=[],
                ),
                validation=IntentValidationOutput(status="valid"),
            ),
            usage_metadata={"total_tokens": 17},
        )


class ScriptedUnderstandingModel:
    """按顺序返回严格 JSON，并记录两个问题理解阶段的提示词。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


def _understanding_response(payload, total_tokens=0):
    content = payload if isinstance(payload, str) else orjson.dumps(payload).decode()
    return QuestionUnderstandingModelResponse(
        content=content,
        usage_metadata={"total_tokens": total_tokens},
    )


def _valid_intent(**overrides):
    payload = {
        "intent_type": "metric_query",
        "confidence": 0.93,
        "metric_mentions": ["销售额"],
        "dimension_mentions": ["城市"],
        "dimension_slots": [
            {
                "name": "城市",
                "role": "group_by",
                "value": None,
                "value_status": "not_provided",
                "value_confidence": 0.9,
            }
        ],
        "time_mentions": ["上个月"],
        "time_range": {"raw": "上个月", "value_status": "provided"},
        "filter_mentions": [],
        "required_slot_types": ["metric", "dimension", "time_dimension"],
        "query_shape": {"needs_group_by": True},
        "ambiguous_slots": [],
        "conflict_slots": [],
    }
    payload.update(overrides)
    return payload


def _valid_dimensions(**overrides):
    payload = {
        "dimension_mentions": ["城市"],
        "dimension_slots": [
            {
                "name": "城市",
                "role": "group_by",
                "value": None,
                "value_status": "not_provided",
                "value_confidence": 0.9,
            }
        ],
        "filter_mentions": [],
        "ambiguous_slots": [],
        "conflict_slots": [],
    }
    payload.update(overrides)
    return payload


class ProbeArgs(BaseModel):
    value: str = ""


class ExecuteSqlFailureArgs(BaseModel):
    sql: str


class ProbeResult(BaseModel):
    value: str


class SearchSemanticAssetsProbeResult(BaseModel):
    status: str
    metrics: list[str]
    dimensions: list[str]
    tables: list[str]


class FinishProbeResult(BaseModel):
    answer: str
    chart: dict
    sql: str | None = None
    non_standard: bool = False


class SearchSemanticAssetsProbeArgs(BaseModel):
    pass


class ProbeTool(AgentTool):
    name = "probe"
    description = "probe"
    args_model = ProbeArgs
    result_model = ProbeResult

    def execute(self, ctx, args):
        ctx.state["last_execution"] = {"sql": "select 1", "fields": ["a"], "row_count": 1, "sql_source": "compiled"}
        ctx.state["full_data"] = [{"a": 1}]
        return ToolResult.succeeded("probed", ProbeResult(value=args.value))


class NoopTool(AgentTool):
    """无副作用探测工具，用于预算/熔断路径（不写入 last_execution）。"""

    name = "noop"
    description = "noop"
    args_model = ProbeArgs
    result_model = ProbeResult

    def execute(self, ctx, args):
        return ToolResult.succeeded("noop", ProbeResult(value=args.value))


class FailingProbeTool(AgentTool):
    """稳定返回执行错误，用于保护普通工具失败后的 Observation 行为。"""

    name = "failing_probe"
    description = "failing probe"
    args_model = ProbeArgs
    result_model = ProbeResult

    def execute(self, ctx, args):
        return ToolResult.failed(
            "探测工具执行失败",
            error_code="probe_failed",
            error_category=ToolErrorCategory.DOMAIN,
            retry_advice=RetryAdvice.NEVER,
        )


class FailingExecuteSqlTool(AgentTool):
    """稳定返回 SQL 执行错误，用于保护 SQL 重试耗尽行为。"""

    name = "execute_sql"
    description = "failing execute sql"
    args_model = ExecuteSqlFailureArgs
    result_model = ProbeResult

    def execute(self, ctx, args):
        return ToolResult.failed(
            "数据库暂不可用",
            error_code="database_unavailable",
            error_category=ToolErrorCategory.DOMAIN,
            retry_advice=RetryAdvice.CORRECT_INPUT,
        )


class SearchSemanticAssetsProbeTool(AgentTool):
    """模拟从运行状态读取意图的无参语义检索工具。"""

    name = "search_semantic_assets"
    description = "search semantic assets"
    args_model = SearchSemanticAssetsProbeArgs
    result_model = SearchSemanticAssetsProbeResult

    def execute(self, ctx, args):
        return ToolResult.succeeded(
            "searched",
            SearchSemanticAssetsProbeResult(
                status="hit",
                metrics=["gmv"],
                dimensions=[],
                tables=[],
            ),
        )


class FinishProbeTool(AgentTool):
    name = "finish"
    description = "finish"
    args_model = ProbeArgs
    result_model = FinishProbeResult

    def execute(self, ctx, args):
        return ToolResult.succeeded(
            "finish",
            FinishProbeResult(
                answer="最终答案",
                chart={},
                sql="select 1",
                non_standard=False,
            ),
        )


def _registry():
    registry = ToolRegistry()
    registry.register(ProbeTool())
    registry.register(NoopTool())
    registry.register(SearchSemanticAssetsProbeTool())
    registry.register(FinishProbeTool())
    return registry


def _run_and_record():
    run = ChatbiAgentRun(oid=1, chat_id=1, record_id=2, status=AgentRunStatus.CREATED.value)
    run.id = 100
    record = ChatRecord(chat_id=1, question="按城市看 gmv", datasource=5)
    record.id = 2
    return run, record


def _loop(model, config=None):
    return build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
    )


def _event_domains(events):
    return [event.domain for event in events]


def _tool_message(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def test_reasoner_returns_structured_function_call_and_records_usage():
    response = AIMessage(
        content="需要查询数据",
        tool_calls=[
            {"name": "probe", "args": {"value": "x"}, "id": "call-1", "type": "tool_call"}
        ],
        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )
    model = ScriptedModel([response])
    run, record = _run_and_record()
    state = AgentRuntimeState(
        run=run,
        record=record,
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5, token_budget=100),
        system=AgentMessage.system("系统提示词"),
    )
    reasoner = AgentReasoner(
        AgentConfig(),
        model,
        _registry(),
        DisabledAgentTracer(),
    )

    decision = reasoner.decide(state, "normal")

    assert decision.reasoning == "需要查询数据"
    assert [(call.name, call.call_id) for call in decision.tool_calls] == [
        ("probe", "call-1")
    ]
    assert state.budget.steps == 1
    assert state.budget.tokens_used == 5
    assert state.messages[-1] is decision.response
    assert state.messages[-1].content == response.content


def test_reasoner_soft_mode_only_exposes_terminal_tools():
    model = ScriptedModel([AIMessage(content="基于现有结果结束")])
    run, record = _run_and_record()
    state = AgentRuntimeState(
        run=run,
        record=record,
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )
    state.context.state["last_execution"] = {
        "sql": "select 1",
        "fields": ["a"],
        "row_count": 1,
    }
    registry = _registry()
    reasoner = AgentReasoner(
        AgentConfig(),
        model,
        registry,
        DisabledAgentTracer(),
    )

    decision = reasoner.decide(state, "soft")

    assert decision.is_direct_answer is True
    assert "预算接近上限" in str(model.calls[0][1].content)
    assert [item.name for item in model.tool_definition_calls[0]] == [
        "finish"
    ]


def test_tool_visibility_follows_chatbi_stage():
    run, record = _run_and_record()
    state = AgentRuntimeState(
        run=run,
        record=record,
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )
    state.context.state["question_understanding"] = {
        "validation": {"status": "valid"}
    }
    registered = [
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

    assert visible_tool_names(state, "normal", registered) == registered[:4]

    state.context.state["semantic_asset_ids"] = [1]
    assert visible_tool_names(state, "normal", registered) == [
        *registered[:4],
        "compile_semantic_sql",
    ]

    state.context.state["allowed_tables"] = ["orders"]
    assert visible_tool_names(state, "normal", registered) == [
        *registered[:4],
        "compile_semantic_sql",
        "validate_sql",
        "execute_sql",
    ]

    state.context.state["semantic_package"] = {"status": "metric_ambiguous"}
    assert visible_tool_names(state, "normal", registered) == ["clarify"]
    assert visible_tool_names(state, "soft", registered) == ["clarify"]

    state.context.state["last_execution"] = {"sql": "select 1"}
    assert visible_tool_names(state, "normal", registered) == ["finish"]
    assert visible_tool_names(state, "soft", registered) == ["finish"]


def test_soft_mode_without_legal_closure_tool_exposes_nothing():
    run, record = _run_and_record()
    state = AgentRuntimeState(
        run=run,
        record=record,
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=5),
        messages=[AgentMessage.user("按城市看 gmv")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )

    assert visible_tool_names(state, "soft", ["clarify", "finish"]) == []


def test_happy_path_tool_then_finish():
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    domains = _event_domains(events)

    assert domains[:3] == ["run.created", "run.started", "question.understood"]
    assert "tool.called" in domains and "tool.completed" in domains
    assert domains[-2:] == ["answer.completed", "run.finished"]
    assert run.status == AgentRunStatus.FINISHED.value
    assert record.status == "succeeded"
    assert record.finish is True
    assert record.finish_time is not None
    assert record.sql_answer == "最终答案"
    assert record.sql == "select 1"
    assert orjson.loads(record.data) == {"fields": ["a"], "data": [{"a": 1}]}
    # 消息历史持久化：human + 2 轮 assistant + 2 条 tool 回写
    assert len(run.messages) == 5
    assert run.derived_state["question_understanding"]["intent"]["metric_mentions"] == ["gmv"]
    assert run.budget_snapshot["tokens_used"] == 17
    assert "时间筛选必须原样使用 `time_range.normalized`" in model.calls[0][0].content


def test_success_events_have_strict_sequence_and_single_terminal_event():
    """重构主循环后仍必须保持事件有序，并且只能产生一个运行终态。"""

    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()

    events = list(_loop(model).run(run, record))
    domains = _event_domains(events)

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [domain for domain in domains if domain in {"run.finished", "run.failed"}] == [
        "run.finished"
    ]
    assert domains[-2:] == ["answer.completed", "run.finished"]


def test_multiple_tool_calls_preserve_model_order_in_events_and_observations():
    """同一推理轮的多个 Tool Call 必须按模型顺序发布并回写。"""

    model = ScriptedModel(
        [
            AIMessage(
                content="需要连续调用两个工具",
                tool_calls=[
                    {
                        "name": "probe",
                        "args": {"value": "first"},
                        "id": "call-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "noop",
                        "args": {"value": "second", "api_token": "secret"},
                        "id": "call-2",
                        "type": "tool_call",
                    },
                ],
            ),
            _tool_message("finish", {"value": ""}, "call-3"),
        ]
    )
    run, record = _run_and_record()

    session = FakeSession()
    loop = build_agent_loop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
    )
    events = list(loop.run(run, record))

    called = [event for event in events if event.domain == "tool.called"]
    completed = [event for event in events if event.domain == "tool.completed"]
    assert [event.content["tool_name"] for event in called[:2]] == [
        "probe",
        "noop",
    ]
    assert [event.content["tool_name"] for event in completed[:2]] == [
        "probe",
        "noop",
    ]
    assert [event.block_id for event in called[:2]] == [
        "tool:100:call-1",
        "tool:100:call-2",
    ]
    assert called[1].content["args_summary"]["api_token"] == "[REDACTED]"

    rows = list(
        {
            id(item): item
            for item in session.added
            if isinstance(item, ChatbiAgentToolCall)
        }.values()
    )
    first_step_rows = [item for item in rows if item.tool_call_id in {"call-1", "call-2"}]
    assert [item.tool_call_id for item in first_step_rows] == ["call-1", "call-2"]
    assert [item.status for item in first_step_rows] == ["succeeded", "succeeded"]
    assert first_step_rows[0].step_id == first_step_rows[1].step_id
    assert session.tool_event_commits[:4] == [
        ("tool-called", "call-1", "running"),
        ("tool-called", "call-2", "running"),
        ("tool-result", "call-1", "succeeded"),
        ("tool-result", "call-2", "succeeded"),
    ]
    first_step = next(
        item
        for item in session.added
        if isinstance(item, ChatbiAgentStep) and item.step_index == 1
    )
    assert first_step.result_summary["tool_call_count"] == 2

    second_turn_tool_messages = [
        message
        for message in model.calls[1]
        if message.role.value == "tool"
    ]
    assert [message.tool_call_id for message in second_turn_tool_messages] == [
        "call-1",
        "call-2",
    ]


def test_regular_tool_error_is_observed_but_cannot_masquerade_as_answer():
    """普通工具失败仍是 Observation，但问数不能在无查询结果时直接成功。"""

    registry = _registry()
    registry.register(FailingProbeTool())
    model = ScriptedModel([
        _tool_message("failing_probe", {"value": "x"}),
        AIMessage(content="工具失败后如实结束。"),
    ])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=registry,
        understanding_service=StaticUnderstandingService(),
    )

    events = list(loop.run(run, record))
    failed_tool_event = next(
        event
        for event in events
        if event.domain == "tool.failed"
        and event.content.get("tool_name") == "failing_probe"
    )

    assert failed_tool_event.content["record_id"] == record.id
    assert failed_tool_event.content["tool_call_id"] == "c1"
    assert failed_tool_event.content["tool_name"] == "failing_probe"
    assert failed_tool_event.content["status"] == "failed"
    assert failed_tool_event.content["error_code"] == "probe_failed"
    assert run.status == AgentRunStatus.FAILED.value
    assert _event_domains(events)[-1] == "run.failed"
    second_call_tool_messages = [
        message
        for message in model.calls[1]
        if message.role.value == "tool"
    ]
    assert any("探测工具执行失败" in message.content for message in second_call_tool_messages)


def test_execute_sql_retry_exhaustion_has_single_failed_terminal_event():
    """SQL 执行错误超过重试上限后必须明确失败，不能继续调用模型。"""

    registry = ToolRegistry()
    registry.register(FailingExecuteSqlTool())
    model = ScriptedModel([
        _tool_message("execute_sql", {"sql": "select 1"}, "sql-1"),
        _tool_message("execute_sql", {"sql": "select 2"}, "sql-2"),
        _tool_message("execute_sql", {"sql": "select 3"}, "sql-3"),
    ])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5, max_sql_retries=1),
        model_client=model,
        registry=registry,
        understanding_service=StaticUnderstandingService(),
    )

    events = list(loop.run(run, record))
    domains = _event_domains(events)

    assert len(model.calls) == 3
    assert domains.count("tool.failed") == 3
    assert [domain for domain in domains if domain in {"run.finished", "run.failed"}] == [
        "run.failed"
    ]
    assert domains[-1] == "run.failed"
    assert run.status == AgentRunStatus.FAILED.value
    assert record.status == "failed"
    assert run.error_class == "sql_failed"
    assert "SQL 修正次数已达上限 1 次" in run.error


def test_agent_tracing_records_run_llm_and_tool_hierarchy():
    tracer = RecordingTracer()
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
        tracer=tracer,
    )

    list(loop.run(run, record))

    assert tracer.spans[0].name == "invoke_agent"
    assert tracer.spans[0].attributes["app.run.id"] == run.id
    assert [span.parent for span in tracer.spans[1:]] == ["invoke_agent"] * 4
    assert [span.name for span in tracer.spans[1:]] == [
        "chat",
        "execute_tool",
        "chat",
        "execute_tool",
    ]
    assert tracer.spans[0].attributes["gen_ai.agent.result"] == "finished"
    tool_spans = [span for span in tracer.spans if span.name == "execute_tool"]
    assert [span.attributes["app.tool_call.id"] for span in tool_spans] == [
        "c1",
        "c2",
    ]
    assert all(span.attributes["app.run.id"] == run.id for span in tool_spans)
    assert all("app.tool.latency_ms" in span.attributes for span in tool_spans)
    assert all(span.attributes["app.domain.retry_count"] == 0 for span in tool_spans)
    assert all(span.closed for span in tracer.spans)


def test_opentelemetry_exporter_receives_agent_span_hierarchy():
    trace_sdk = pytest.importorskip("opentelemetry.sdk.trace")
    trace_export = pytest.importorskip("opentelemetry.sdk.trace.export")
    memory_export = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )
    provider = trace_sdk.TracerProvider()
    exporter = memory_export.InMemorySpanExporter()
    provider.add_span_processor(trace_export.SimpleSpanProcessor(exporter))
    tracer = ResilientAgentTracer(
        OpenTelemetryAgentTracer(provider.get_tracer("numora-agent-test"))
    )
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
        tracer=tracer,
    )

    list(loop.run(run, record))

    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "invoke_agent")
    children = [span for span in spans if span.name in {"chat", "execute_tool"}]
    assert [span.name for span in children] == [
        "chat",
        "execute_tool",
        "chat",
        "execute_tool",
    ]
    assert all(span.parent and span.parent.span_id == root.context.span_id for span in children)
    assert root.attributes["gen_ai.agent.result"] == "finished"


def test_agent_span_closes_when_event_generator_is_closed():
    tracer = RecordingTracer()
    model = ScriptedModel([AIMessage(content="完成")])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
        tracer=tracer,
    )

    events = loop.run(run, record)
    next(events)
    events.close()

    assert tracer.spans[0].name == "invoke_agent"
    assert tracer.spans[0].closed is True


def test_disabled_and_zero_sampling_do_not_load_opentelemetry(monkeypatch):
    build_agent_tracer.cache_clear()
    monkeypatch.setattr(
        "apps.trace.setup.import_module",
        lambda name: pytest.fail(f"不应加载 OpenTelemetry: {name}"),
    )

    assert isinstance(build_agent_tracer(TraceConfig(enabled=False)), DisabledAgentTracer)
    assert isinstance(
        build_agent_tracer(TraceConfig(enabled=True, sample_rate=0)),
        DisabledAgentTracer,
    )


def test_enabled_tracing_without_dependencies_raises_clear_import_error(monkeypatch):
    build_agent_tracer.cache_clear()

    def missing_dependency(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("apps.trace.setup.import_module", missing_dependency)

    with pytest.raises(ImportError, match="observability"):
        build_agent_tracer(TraceConfig(enabled=True, sample_rate=1))


def test_runtime_tracing_failure_does_not_change_agent_events():
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
        tracer=ResilientAgentTracer(FailingTracer()),
    )

    domains = _event_domains(list(loop.run(run, record)))

    assert domains[-2:] == ["answer.completed", "run.finished"]


def test_problem_rewrite_only_receives_last_rewritten_question(monkeypatch):
    captured_context = {}

    class CapturingUnderstandingService(StaticUnderstandingService):
        def understand(self, *, question, datasource_id, conversation_context=None):
            captured_context.update(conversation_context or {})
            return super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )

    monkeypatch.setattr(
        "apps.chatbi.orchestration.agent.preparation.agent_run_repository.recent_qa_summaries",
        lambda session, chat_id, exclude_record_id, limit: [
            {
                "question": "今天店铺的客户数",
                "sql": "select previous_month",
                "answer_brief": "上个月各店铺的销售下单客户数",
            }
        ],
    )
    monkeypatch.setattr(
        "apps.chatbi.orchestration.agent.preparation.agent_run_repository.latest_successful_rewritten_question",
        lambda session, **kwargs: "今天按店铺分组的销售下单客户数",
    )

    model = ScriptedModel([AIMessage(content="完成")])
    run, record = _run_and_record()
    loop = build_agent_loop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=CapturingUnderstandingService(),
    )

    list(loop.run(run, record))

    assert captured_context == {
        "last_rewritten_question": "今天按店铺分组的销售下单客户数"
    }


def test_search_semantic_assets_trace_records_effective_understanding_input():
    model = ScriptedModel(
        [
            _tool_message("search_semantic_assets", {}),
            AIMessage(content="已完成语义检索。"),
        ]
    )
    run, record = _run_and_record()

    events = list(_loop(model).run(run, record))

    tool_event = next(item for item in events if item.domain == "tool.called")
    assert tool_event.content["args_summary"]["rewritten_question"] == "按城市看 gmv"
    assert tool_event.content["args_summary"]["intent"]["metric_mentions"] == ["gmv"]


def test_data_question_direct_text_without_execution_is_rejected():
    model = ScriptedModel([AIMessage(content="这个问题不需要查数据：答案是 42。")])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    domains = _event_domains(events)

    assert "reasoning.snapshot" in domains
    assert domains[-1] == "run.failed"
    assert run.status == AgentRunStatus.FAILED.value
    assert run.error_class == "sql_failed"
    assert record.sql_answer is None


def test_budget_exhaustion_fails_run_honestly():
    responses = [_tool_message("noop", {"value": str(i)}, f"c{i}") for i in range(10)]
    model = ScriptedModel(responses)
    run, record = _run_and_record()
    events = list(_loop(model, AgentConfig(max_steps=2)).run(run, record))
    domains = _event_domains(events)

    assert domains[-1:] == ["run.failed"]
    assert run.status == AgentRunStatus.FAILED.value
    assert record.status == "failed"
    assert record.finish is True
    assert record.finish_time is not None
    assert run.error_class == "budget_exhausted"


def test_budget_exhaustion_soft_wraps_when_execution_exists():
    responses = [_tool_message("probe", {"value": str(i)}, f"c{i}") for i in range(10)]
    model = ScriptedModel(responses)
    run, record = _run_and_record()
    events = list(_loop(model, AgentConfig(max_steps=2)).run(run, record))
    domains = _event_domains(events)

    assert domains[-2:] == ["answer.completed", "run.finished"]
    assert run.status == AgentRunStatus.FINISHED.value
    assert "预算已达上限" in record.sql_answer
    assert record.sql == "select 1"


def test_repeat_fuse_fails_run():
    responses = [_tool_message("noop", {"value": "same"}, f"c{i}") for i in range(5)]
    model = ScriptedModel(responses)
    run, record = _run_and_record()
    list(_loop(model, AgentConfig(max_steps=10, repeat_fuse_threshold=3)).run(run, record))

    assert run.status == AgentRunStatus.FAILED.value
    assert run.error_class == "budget_exhausted"
    assert "重复熔断" in run.error


def test_unknown_tool_is_rejected_and_direct_data_answer_still_fails():
    model = ScriptedModel([
        _tool_message("hack_tool", {"x": 1}),
        AIMessage(content="好的，我换个方式直接回答。"),
    ])
    run, record = _run_and_record()
    list(_loop(model).run(run, record))

    assert run.status == AgentRunStatus.FAILED.value
    # 第二轮的消息历史里包含拒绝回写
    second_call_messages = model.calls[1]
    tool_messages = [m for m in second_call_messages if m.role.value == "tool"]
    assert any("不在白名单" in m.content for m in tool_messages)
    # dangling tool_calls 已收口
    assert any(getattr(m, "tool_call_id", None) for m in tool_messages)


def test_understanding_rewrites_followup_before_recognizing_intent():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "followup",
                    "rewritten_question": "按城市统计上个月销售额",
                    "inherited_context": {"metric": "销售额", "dimension": "城市"},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.96,
                },
                total_tokens=30,
            ),
            _understanding_response(_valid_intent(), total_tokens=40),
            _understanding_response(_valid_dimensions(), total_tokens=10),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="那上个月呢",
        datasource_id=5,
        conversation_context={"last_rewritten_question": "按城市统计本月销售额"},
    )

    assert outcome.output.rewritten_question == "按城市统计上个月销售额"
    assert outcome.output.intent.metric_mentions == ["销售额"]
    assert outcome.output.intent.time_range.normalized == {
        "kind": "previous_period",
        "unit": "month",
        "timezone": "Asia/Shanghai",
    }
    assert outcome.output.validation.status == "valid"
    assert outcome.usage_metadata["total_tokens"] == 80
    intent_request = orjson.loads(model.calls[1][1])
    assert intent_request["rewritten_question"] == "按城市统计上个月销售额"
    assert "那上个月呢" not in model.calls[1][1]


def test_understanding_normalizes_today_before_agent_planning():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "今天店铺的客户数",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.98,
                }
            ),
            _understanding_response(
                _valid_intent(
                    metric_mentions=["客户数"],
                    dimension_mentions=[],
                    dimension_slots=[],
                    time_mentions=["今天"],
                    time_range={"raw": "今天", "value_status": "provided"},
                    required_slot_types=["metric", "time_range"],
                    query_shape={},
                )
            ),
            _understanding_response(
                _valid_dimensions(
                    dimension_mentions=["店铺"],
                    dimension_slots=[
                        {
                            "name": "店铺",
                            "role": "ambiguous",
                            "value": None,
                            "value_status": "not_provided",
                            "value_confidence": 0.95,
                        }
                    ],
                    ambiguous_slots=["店铺"],
                )
            ),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="今天店铺的客户数",
        datasource_id=5,
    )

    assert outcome.output.intent.time_range.normalized == {
        "kind": "single_date",
        "anchor": "today",
        "offset_days": 0,
        "timezone": "Asia/Shanghai",
    }
    assert outcome.output.intent.dimension_mentions == ["店铺"]
    assert outcome.output.validation.status == "clarification_required"
    assert "dimension_role_ambiguous" in outcome.output.validation.reason_codes


def test_understanding_rejects_filter_dimension_without_concrete_value():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "今天店铺的客户数",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.98,
                }
            ),
            _understanding_response(
                _valid_intent(
                    metric_mentions=["客户数"],
                    dimension_mentions=["店铺"],
                    dimension_slots=[
                        {
                            "name": "店铺",
                            "role": "filter",
                            "value": None,
                            "value_status": "not_provided",
                            "value_confidence": 0.0,
                        }
                    ],
                    time_mentions=["今天"],
                    time_range={"raw": "今天", "value_status": "provided"},
                    required_slot_types=["filter"],
                    query_shape={},
                )
            ),
            _understanding_response(
                _valid_dimensions(
                    dimension_mentions=["店铺"],
                    dimension_slots=[
                        {
                            "name": "店铺",
                            "role": "filter",
                            "value": None,
                            "value_status": "not_provided",
                            "value_confidence": 0.0,
                        }
                    ],
                )
            ),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="今天店铺的客户数",
        datasource_id=5,
    )

    assert outcome.output.validation.status == "clarification_required"
    assert "dimension_filter_value_missing" in outcome.output.validation.reason_codes
    assert "filter_value" in outcome.output.validation.clarification_slots


def test_understanding_does_not_treat_ambiguous_dimension_role_as_missing_filter_value():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "今天店铺的客户数",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.98,
                }
            ),
            _understanding_response(
                _valid_intent(
                    metric_mentions=["客户数"],
                    dimension_mentions=["店铺"],
                    dimension_slots=[
                        {
                            "name": "店铺",
                            "role": "ambiguous",
                            "value": None,
                            "value_status": "ambiguous",
                            "value_confidence": 0.0,
                        }
                    ],
                    ambiguous_slots=["店铺"],
                )
            ),
            _understanding_response(
                _valid_dimensions(
                    dimension_mentions=["店铺"],
                    dimension_slots=[
                        {
                            "name": "店铺",
                            "role": "ambiguous",
                            "value": None,
                            "value_status": "ambiguous",
                            "value_confidence": 0.0,
                        }
                    ],
                    ambiguous_slots=["店铺"],
                )
            ),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="今天店铺的客户数",
        datasource_id=5,
    )

    assert outcome.output.validation.status == "clarification_required"
    assert outcome.output.validation.clarification_slots == ["dimension"]
    assert "dimension_value_ambiguous" not in outcome.output.validation.reason_codes


def test_understanding_requires_clarification_for_unsupported_time_range():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "发薪日销售额",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.98,
                }
            ),
            _understanding_response(
                _valid_intent(
                    time_mentions=["发薪日"],
                    time_range={"raw": "发薪日", "value_status": "provided"},
                )
            ),
            _understanding_response(
                _valid_dimensions(dimension_mentions=[], dimension_slots=[])
            ),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="发薪日销售额",
        datasource_id=5,
    )

    assert outcome.output.intent.time_range.normalized == {
        "kind": "unsupported",
        "raw": "发薪日",
        "timezone": "Asia/Shanghai",
    }
    assert outcome.output.validation.status == "clarification_required"
    assert "time_range_unsupported" in outcome.output.validation.reason_codes
    assert "time_range" in outcome.output.validation.clarification_slots


def test_understanding_marks_missing_metric_for_clarification_without_guessing():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "看一下北京最近7天的数据",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.98,
                }
            ),
            _understanding_response(
                _valid_intent(
                    metric_mentions=[],
                    dimension_mentions=["地区"],
                    dimension_slots=[
                        {
                            "name": "地区",
                            "role": "filter",
                            "value": "北京",
                            "value_status": "provided",
                            "value_confidence": 0.9,
                        }
                    ],
                    time_mentions=["最近7天"],
                    time_range={"raw": "最近7天", "value_status": "provided"},
                )
            ),
            _understanding_response(
                _valid_dimensions(
                    dimension_mentions=["地区"],
                    dimension_slots=[
                        {
                            "name": "地区",
                            "role": "filter",
                            "value": "北京",
                            "value_status": "provided",
                            "value_confidence": 0.9,
                        }
                    ],
                )
            ),
        ]
    )

    outcome = QuestionUnderstandingService(model).understand(
        question="看一下北京最近7天的数据",
        datasource_id=5,
    )

    assert outcome.output.intent.metric_mentions == []
    assert outcome.output.validation.status == "clarification_required"
    assert "metric_missing" in outcome.output.validation.reason_codes
    assert "metric" in outcome.output.validation.clarification_slots


def test_understanding_rejects_non_json_without_silent_fallback():
    model = ScriptedUnderstandingModel([_understanding_response("不是 JSON")])

    with pytest.raises(QuestionUnderstandingError, match="QUESTION_REWRITE_MODEL_OUTPUT_NOT_JSON"):
        QuestionUnderstandingService(model).understand(question="本月销售额", datasource_id=5)

    assert len(model.calls) == 1


def test_understanding_rejects_fields_outside_contract():
    model = ScriptedUnderstandingModel(
        [
            _understanding_response(
                {
                    "message_type": "new_question",
                    "rewritten_question": "本月销售额",
                    "inherited_context": {},
                    "need_user_input": False,
                    "missing_slots": [],
                    "confidence": 0.9,
                    "tool_name": "search_semantic_assets",
                }
            )
        ]
    )

    with pytest.raises(QuestionUnderstandingError, match="QUESTION_REWRITE_MODEL_OUTPUT_INVALID"):
        QuestionUnderstandingService(model).understand(question="本月销售额", datasource_id=5)
