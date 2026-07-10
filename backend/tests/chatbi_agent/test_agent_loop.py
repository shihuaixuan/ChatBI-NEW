"""AgentLoop 端到端行为测试：FakeSession + 脚本化模型客户端，不依赖真实 DB/LLM。"""

from types import SimpleNamespace

import orjson
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from apps.chat.models.chat_model import ChatRecord
from apps.chatbi_agent.loop import AgentLoop
from apps.chatbi_agent.models import AgentRunStatus, ChatbiAgentRun, ChatbiAgentTraceEvent
from apps.chatbi_agent.schemas import AgentConfig
from apps.chatbi_agent.tools.base import AgentTool, ToolOutput
from apps.chatbi_agent.tools.registry import ToolRegistry


class FakeSession:
    def __init__(self):
        self.trace_count = 0

    def add(self, obj):
        if isinstance(obj, ChatbiAgentTraceEvent):
            self.trace_count += 1

    def commit(self):
        pass

    def flush(self):
        pass

    def refresh(self, obj):
        pass

    def exec(self, stmt):
        count = self.trace_count
        return SimpleNamespace(scalar=lambda: count, scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None))


class ScriptedModel:
    """按脚本依次返回 AIMessage。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages, tool_specs):
        self.calls.append(messages)
        return self.responses.pop(0)


class ProbeArgs(BaseModel):
    value: str = ""


class ProbeTool(AgentTool):
    name = "probe"
    description = "probe"
    args_model = ProbeArgs

    def execute(self, ctx, args):
        ctx.state["last_execution"] = {"sql": "select 1", "fields": ["a"], "row_count": 1, "sql_source": "compiled"}
        ctx.state["full_data"] = [{"a": 1}]
        return ToolOutput(success=True, summary="probed", payload={"value": args.value})


class FinishProbeTool(AgentTool):
    name = "finish"
    description = "finish"
    args_model = ProbeArgs

    def execute(self, ctx, args):
        return ToolOutput(
            success=True,
            summary="finish",
            payload={"answer": "最终答案", "chart": {}, "sql": "select 1", "non_standard": False},
        )


def _registry():
    registry = ToolRegistry()
    registry.register(ProbeTool())
    registry.register(FinishProbeTool())
    return registry


def _run_and_record():
    run = ChatbiAgentRun(oid=1, chat_id=1, record_id=2, status=AgentRunStatus.CREATED.value)
    run.id = 100
    record = ChatRecord(chat_id=1, question="按城市看 gmv", datasource=5)
    record.id = 2
    return run, record


def _loop(model, config=None):
    return AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
    )


def _event_types(events):
    return [orjson.loads(event.removeprefix("data:"))["type"] for event in events]


def _tool_message(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def test_happy_path_tool_then_finish():
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c2"),
    ])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    types = _event_types(events)

    assert types[:2] == ["record-created", "run-started"]
    assert "tool-called" in types and "tool-result" in types
    assert types[-3:] == ["answer", "run-finished", "finish"]
    assert run.status == AgentRunStatus.FINISHED.value
    assert record.sql_answer == "最终答案"
    assert record.sql == "select 1"
    assert orjson.loads(record.data) == {"fields": ["a"], "data": [{"a": 1}]}
    # 消息历史持久化：human + 2 轮 assistant + 2 条 tool 回写
    assert len(run.messages) == 5


def test_direct_text_treated_as_loose_finish():
    model = ScriptedModel([AIMessage(content="这个问题不需要查数据：答案是 42。")])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    types = _event_types(events)

    assert "thinking" in types
    assert types[-3:] == ["answer", "run-finished", "finish"]
    assert run.status == AgentRunStatus.FINISHED.value
    assert "42" in record.sql_answer


def test_budget_exhaustion_fails_run_honestly():
    responses = [_tool_message("probe", {"value": str(i)}, f"c{i}") for i in range(10)]
    model = ScriptedModel(responses)
    run, record = _run_and_record()
    events = list(_loop(model, AgentConfig(max_steps=2)).run(run, record))
    types = _event_types(events)

    assert types[-2:] == ["run-failed", "error"]
    assert run.status == AgentRunStatus.FAILED.value
    assert run.error_class == "budget_exhausted"


def test_repeat_fuse_fails_run():
    responses = [_tool_message("probe", {"value": "same"}, f"c{i}") for i in range(5)]
    model = ScriptedModel(responses)
    run, record = _run_and_record()
    events = list(_loop(model, AgentConfig(max_steps=10, repeat_fuse_threshold=3)).run(run, record))

    assert run.status == AgentRunStatus.FAILED.value
    assert run.error_class == "budget_exhausted"
    assert "重复熔断" in run.error


def test_unknown_tool_is_rejected_but_loop_continues():
    model = ScriptedModel([
        _tool_message("hack_tool", {"x": 1}),
        AIMessage(content="好的，我换个方式直接回答。"),
    ])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))

    assert run.status == AgentRunStatus.FINISHED.value
    # 第二轮的消息历史里包含拒绝回写
    second_call_messages = model.calls[1]
    tool_messages = [m for m in second_call_messages if m.__class__.__name__ == "ToolMessage"]
    assert any("不在白名单" in m.content for m in tool_messages)
