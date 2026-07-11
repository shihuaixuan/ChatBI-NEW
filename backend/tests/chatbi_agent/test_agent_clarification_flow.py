"""P1 交互：clarify 挂起/恢复、澄清预算、上下文折叠、prompt 注入。"""

from types import SimpleNamespace

import orjson
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat.models.chat_model import ChatRecord
from apps.chatbi_agent.loop import FOLDED_PLACEHOLDER, AgentLoop, _fold_messages
from apps.chatbi_agent.models import AgentRunStatus, ChatbiAgentRun, ChatbiAgentTraceEvent
from apps.chatbi_agent.prompts import build_system_prompt
from apps.chatbi_agent.schemas import AgentConfig
from apps.chatbi_agent.tools.registry import ToolRegistry
from apps.chatbi_agent.tools.interaction import ClarifyTool
from tests.chatbi_agent.test_agent_loop import (
    FakeSession,
    FinishProbeTool,
    ProbeTool,
    ScriptedModel,
    _event_types,
    _tool_message,
)


def _registry_with_clarify():
    registry = ToolRegistry()
    registry.register(ProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    return registry


def _run_and_record():
    run = ChatbiAgentRun(oid=1, chat_id=1, record_id=2, status=AgentRunStatus.CREATED.value)
    run.id = 100
    record = ChatRecord(chat_id=1, question="额度趋势", datasource=5)
    record.id = 2
    return run, record


def _loop(model, config=None):
    return AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=6),
        model_client=model,
        registry=_registry_with_clarify(),
    )


def test_clarify_suspends_run_and_persists_messages():
    model = ScriptedModel([
        _tool_message("probe", {"value": "warm"}),
        _tool_message("clarify", {"question": "你要查哪种额度？", "options": [{"label": "授信额度", "value": "credit"}]}, "c2"),
    ])
    run, record = _run_and_record()
    events = list(_loop(model).run(run, record))
    types = _event_types(events)

    assert types[-1] == "clarification"
    payload = orjson.loads(events[-1].removeprefix("data:"))["content"]
    assert payload["question"] == "你要查哪种额度？"
    assert payload["options"][0]["label"] == "授信额度"
    assert run.status == AgentRunStatus.WAITING_USER.value
    assert run.budget_snapshot["clarifications"] == 1
    # 消息历史保留了带未回填 tool_call 的 assistant 消息
    assert run.messages[-1]["type"] == "ai"
    # 派生状态随挂起持久化（不含全量数据），恢复后回填避免重复检索
    assert run.derived_state["last_execution"]["sql"] == "select 1"
    assert "full_data" not in run.derived_state


def test_resume_restores_derived_state_into_tool_context():
    run, record = _run_and_record()
    run.messages = [{"type": "human", "data": {"content": "q", "type": "human"}}]
    run.derived_state = {"semantic_asset_ids": [7, 8], "allowed_tables": ["t1"], "question": "q"}
    captured = {}

    class StateProbeTool(ProbeTool):
        name = "probe"

        def execute(self, ctx, args):
            captured.update(ctx.state)
            return super().execute(ctx, args)

    registry = ToolRegistry()
    registry.register(StateProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c9"),
    ])
    loop = AgentLoop(FakeSession(), SimpleNamespace(id=1, oid=1), AgentConfig(max_steps=6), model_client=model, registry=registry)
    clarification = SimpleNamespace(tool_call_id="prev")
    list(loop.resume(run, record, clarification, "答"))

    assert captured["semantic_asset_ids"] == [7, 8]
    assert captured["allowed_tables"] == ["t1"]


def test_resume_continues_from_clarification_to_finish():
    suspend_model = ScriptedModel([
        _tool_message("clarify", {"question": "哪种额度？", "options": []}, "call_clarify"),
    ])
    run, record = _run_and_record()
    list(_loop(suspend_model).run(run, record))
    assert run.status == AgentRunStatus.WAITING_USER.value

    resume_model = ScriptedModel([
        _tool_message("probe", {"value": "x"}),
        _tool_message("finish", {"value": ""}, "c9"),
    ])
    clarification = SimpleNamespace(tool_call_id="call_clarify")
    events = list(_loop(resume_model).resume(run, record, clarification, "用户澄清回答：授信额度"))
    types = _event_types(events)

    assert types[0] == "clarification-accepted"
    assert types[-3:] == ["answer", "run-finished", "finish"]
    assert run.status == AgentRunStatus.FINISHED.value
    # 恢复后的首轮消息里包含澄清答案 ToolMessage
    first_call = resume_model.calls[0]
    tool_messages = [m for m in first_call if isinstance(m, ToolMessage)]
    assert any("授信额度" in m.content for m in tool_messages)
    # 预算从快照恢复：挂起时 1 步 + 恢复后 2 步
    assert run.budget_snapshot["steps"] == 3
    assert run.budget_snapshot["clarifications"] == 1


def test_clarify_over_budget_rejected_and_loop_continues():
    model = ScriptedModel([
        _tool_message("clarify", {"question": "q1", "options": []}, "c1"),
    ])
    run, record = _run_and_record()
    # 快照造成澄清已达上限
    run.budget_snapshot = {"clarifications": 2}
    clarification = SimpleNamespace(tool_call_id="prev")
    run.messages = [
        {"type": "human", "data": {"content": "额度趋势", "type": "human"}},
    ]
    resume_model = ScriptedModel([
        _tool_message("clarify", {"question": "再问一次？", "options": []}, "c2"),
        AIMessage(content="好的，基于现有信息直接回答。"),
    ])
    events = list(_loop(resume_model).resume(run, record, clarification, "回答"))
    types = _event_types(events)

    assert "clarification" not in types  # 未再次挂起
    assert run.status == AgentRunStatus.FINISHED.value
    rejected = [m for m in resume_model.calls[1] if isinstance(m, ToolMessage) and "上限" in str(m.content)]
    assert rejected


def test_fold_messages_folds_old_tool_results_only():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content=""),
        ToolMessage(content="x" * 500, tool_call_id="a"),
        AIMessage(content=""),
        ToolMessage(content="y" * 500, tool_call_id="b"),
    ]
    _fold_messages(messages, max_chars=100, keep_recent=2)
    assert messages[2].content == FOLDED_PLACEHOLDER
    assert messages[4].content == "y" * 500  # 最近窗口不折叠
    assert messages[0].content == "q"  # 非工具消息不折叠


def test_system_prompt_injects_history_and_pending_clarification():
    prompt = build_system_prompt(
        datasource_id=5,
        oid=1,
        history_summary="- 问：上月 GMV\n  SQL：select 1\n  答（摘要）：100 万",
        pending_clarification={"question": "哪种额度？", "options": [{"label": "授信"}]},
    )
    assert "最近对话" in prompt
    assert "上月 GMV" in prompt
    assert "挂起的澄清上下文" in prompt
    assert "哪种额度？" in prompt
    assert "判别规则" in prompt


def test_system_prompt_omits_optional_sections():
    prompt = build_system_prompt(datasource_id=5, oid=1)
    assert "最近对话" not in prompt
    assert "挂起的澄清上下文" not in prompt
