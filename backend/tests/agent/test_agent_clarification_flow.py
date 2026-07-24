"""P1 交互：clarify 挂起/恢复、澄清预算、上下文折叠、prompt 注入。"""

from types import SimpleNamespace

import orjson
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.ai_model.openai.llm import BaseChatOpenAI
from apps.chatbi.models import (
    AgentClarificationResumeKind,
    AgentConfig,
    AgentRunStatus,
    ChatbiAgentClarification,
    ChatbiAgentRun,
    DimensionSlot,
)
from apps.chatbi.orchestration.agent.loop import AgentLoop
from apps.chatbi.orchestration.agent.prompts import build_system_prompt
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.tool import FOLDED_PLACEHOLDER, ToolRegistry, fold_tool_messages
from apps.conversation.models import ChatRecord
from tests.agent.test_agent_loop import (
    FakeSession,
    FinishProbeTool,
    ProbeTool,
    ScriptedModel,
    StaticUnderstandingService,
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


def _ambiguous_store_understanding_state():
    return {
        "question": "今天店铺的客户数",
        "question_understanding": {
            "original_question": "今天店铺的客户数",
            "message_type": "new_question",
            "rewritten_question": "今天店铺的客户数",
            "inherited_context": {},
            "intent": {
                "intent_type": "metric_query",
                "confidence": 0.95,
                "metric_mentions": ["客户数"],
                "dimension_mentions": ["店铺"],
                "dimension_slots": [
                    {
                        "name": "店铺",
                        "role": "ambiguous",
                        "value": None,
                        "value_status": "ambiguous",
                    }
                ],
                "ambiguous_slots": ["店铺"],
            },
            "validation": {
                "status": "clarification_required",
                "reason_codes": ["intent_ambiguous", "dimension_role_ambiguous"],
                "clarification_slots": ["dimension"],
            },
        },
    }


def _loop(model, config=None):
    return AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=6),
        model_client=model,
        registry=_registry_with_clarify(),
        understanding_service=StaticUnderstandingService(rewritten_question="额度趋势"),
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
    assert record.status == "waiting_user"
    assert record.finish is False
    assert record.finish_time is None
    assert run.budget_snapshot["clarifications"] == 1
    # 消息历史保留了带未回填 tool_call 的 assistant 消息
    assert run.messages[-1]["type"] == "ai"
    # 派生状态随挂起持久化（不含全量数据），恢复后回填避免重复检索
    assert run.derived_state["last_execution"]["sql"] == "select 1"
    assert "full_data" not in run.derived_state


def test_dimension_role_ambiguity_suspends_before_agent_planning_and_retrieval():
    class AmbiguousDimensionUnderstandingService(StaticUnderstandingService):
        def understand(self, *, question, datasource_id, conversation_context=None):
            outcome = super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )
            output = outcome.output.model_copy(
                update={
                    "rewritten_question": "今天店铺的客户数",
                    "intent": outcome.output.intent.model_copy(
                        update={
                            "metric_mentions": ["客户数"],
                            "dimension_mentions": ["店铺"],
                            "dimension_slots": [
                                DimensionSlot(
                                    name="店铺",
                                    role="ambiguous",
                                    value=None,
                                    value_status="ambiguous",
                                )
                            ],
                            "ambiguous_slots": ["店铺"],
                        }
                    ),
                    "validation": outcome.output.validation.model_copy(
                        update={
                            "status": "clarification_required",
                            "reason_codes": ["intent_ambiguous", "dimension_role_ambiguous"],
                            "clarification_slots": ["dimension"],
                        }
                    ),
                }
            )
            return outcome.__class__(output=output, usage_metadata=outcome.usage_metadata)

    model = ScriptedModel([])
    run, record = _run_and_record()
    record.question = "今天店铺的客户数"
    session = FakeSession()
    loop = AgentLoop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=_registry_with_clarify(),
        understanding_service=AmbiguousDimensionUnderstandingService(),
    )

    events = list(loop.run(run, record))
    types = _event_types(events)

    assert model.calls == []
    assert "tool-result" not in types
    assert types[-4:] == ["step-started", "thinking", "workflow-step", "clarification"]
    assert run.status == AgentRunStatus.WAITING_USER.value
    assert run.budget_snapshot["steps"] == 1
    assert run.budget_snapshot["clarifications"] == 1
    # 工作流澄清没有伪造助手工具调用，挂起前只保留规范化后的用户问题。
    assert [message["type"] for message in run.messages] == ["human"]
    clarification_payload = orjson.loads(events[-1].removeprefix("data:"))["content"]
    assert clarification_payload["question"] == "请确认“店铺”在本次查询中的使用方式。"
    assert [item["value"] for item in clarification_payload["options"]] == [
        "group_by:店铺",
        "filter:店铺",
        "ignore:店铺",
    ]
    clarification = next(item for item in session.added if isinstance(item, ChatbiAgentClarification))
    assert clarification.resume_kind == AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value
    assert clarification.resume_payload == {"operation": "set_dimension_role", "slot_name": "店铺"}


def test_openai_payload_preserves_reasoning_content_for_tool_call_history():
    model = BaseChatOpenAI(model="test-model", api_key="test-key")
    messages = [
        HumanMessage(content="今天店铺的客户数"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "需要先确认店铺的使用方式。"},
            tool_calls=[
                {
                    "name": "clarify",
                    "args": {"question": "店铺用于分组还是筛选？"},
                    "id": "clarify-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="按店铺分组", tool_call_id="clarify-1"),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "需要先确认店铺的使用方式。"
    assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == "clarify"


def test_openai_payload_does_not_add_reasoning_content_to_regular_message():
    model = BaseChatOpenAI(model="test-model", api_key="test-key")

    payload = model._get_request_payload([HumanMessage(content="你好"), AIMessage(content="你好")])

    assert "reasoning_content" not in payload["messages"][1]


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
    loop = AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=registry,
        understanding_service=StaticUnderstandingService(),
    )
    clarification = SimpleNamespace(
        tool_call_id="prev",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "答"},
        question="请补充信息",
        options=[],
    )
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
    clarification = SimpleNamespace(
        tool_call_id="call_clarify",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "授信额度"},
        question="哪种额度？",
        options=[],
    )
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


def test_resume_emits_acceptance_without_reunderstanding():
    class TrackingUnderstandingService(StaticUnderstandingService):
        def __init__(self):
            super().__init__(rewritten_question="用户澄清后的完整问题")
            self.called = False

        def understand(self, *, question, datasource_id, conversation_context=None):
            self.called = True
            return super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )

    run, record = _run_and_record()
    run.status = AgentRunStatus.WAITING_USER.value
    run.messages = [{"type": "human", "data": {"content": "今天店铺的客户数", "type": "human"}}]
    run.derived_state = _ambiguous_store_understanding_state()
    understanding_service = TrackingUnderstandingService()
    loop = AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=ScriptedModel([]),
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={"selections": [{"label": "按店铺分组", "value": "group_by:店铺"}], "text": None},
        question="请确认店铺用法",
        options=[],
    )

    events = loop.resume(run, record, clarification, "按店铺分组")
    first_event = next(events)

    assert _event_types([first_event]) == ["clarification-accepted"]
    assert not understanding_service.called


def test_filter_role_clarification_resumes_to_targeted_value_clarification():
    run, record = _run_and_record()
    run.status = AgentRunStatus.WAITING_USER.value
    run.messages = [{"type": "human", "data": {"content": "今天店铺的客户数", "type": "human"}}]
    run.derived_state = _ambiguous_store_understanding_state()
    session = FakeSession()
    understanding_service = StaticUnderstandingService()
    loop = AgentLoop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=ScriptedModel([]),
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={"selections": [{"label": "筛选具体店铺", "value": "filter:店铺"}], "text": None},
        question="请确认店铺用法",
        options=[],
    )

    events = list(loop.resume(run, record, clarification, "用户澄清回答：筛选具体店铺"))

    assert _event_types(events)[-1] == "clarification"
    assert run.status == AgentRunStatus.WAITING_USER.value
    updated_slot = run.derived_state["question_understanding"]["intent"]["dimension_slots"][0]
    assert updated_slot["role"] == "filter"
    assert updated_slot["value_status"] == "not_provided"
    next_clarification = [
        item for item in session.added if isinstance(item, ChatbiAgentClarification)
    ][-1]
    assert next_clarification.resume_payload == {
        "operation": "set_dimension_filter_value",
        "slot_name": "店铺",
    }
    next_clarification.answer = {"selections": [], "text": "1号店铺"}
    finish_model = ScriptedModel([AIMessage(content="查询完成")])
    finish_loop = AgentLoop(
        session,
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=finish_model,
        registry=_registry_with_clarify(),
        understanding_service=understanding_service,
    )

    finish_events = list(
        finish_loop.resume(run, record, next_clarification, "用户澄清回答：1号店铺")
    )

    assert _event_types(finish_events)[:2] == ["clarification-accepted", "question-understood"]
    assert run.status == AgentRunStatus.FINISHED.value
    confirmed = run.derived_state["question_understanding"]
    assert confirmed["rewritten_question"] == "今天店铺的客户数"
    assert confirmed["intent"]["dimension_slots"][0]["value"] == "1号店铺"
    assert confirmed["validation"]["status"] == "valid"


def test_clarify_over_budget_rejected_and_loop_continues():
    run, record = _run_and_record()
    # 快照造成澄清已达上限
    run.budget_snapshot = {"clarifications": 2}
    clarification = SimpleNamespace(
        tool_call_id="prev",
        resume_kind=AgentClarificationResumeKind.AGENT_TOOL.value,
        resume_payload={},
        answer={"selections": [], "text": "回答"},
        question="请补充信息",
        options=[],
    )
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


def test_resume_updates_target_slot_without_rewriting_or_reunderstanding():
    run, record = _run_and_record()
    run.messages = [{"type": "human", "data": {"content": "今天店铺的客户数", "type": "human"}}]
    run.derived_state = _ambiguous_store_understanding_state()
    run.derived_state["semantic_asset_ids"] = [272, 276]
    captured_state = {}

    class TrackingUnderstandingService(StaticUnderstandingService):
        def __init__(self):
            super().__init__()
            self.called = False

        def understand(self, *, question, datasource_id, conversation_context=None):
            self.called = True
            return super().understand(
                question=question,
                datasource_id=datasource_id,
                conversation_context=conversation_context,
            )

    class StateProbeTool(ProbeTool):
        name = "probe"

        def execute(self, ctx, args):
            captured_state.update(ctx.state)
            return super().execute(ctx, args)

    registry = ToolRegistry()
    registry.register(StateProbeTool())
    registry.register(FinishProbeTool())
    registry.register(ClarifyTool())
    model = ScriptedModel(
        [
            _tool_message("probe", {"value": "x"}),
            _tool_message("finish", {"value": ""}, "c9"),
        ]
    )
    understanding_service = TrackingUnderstandingService()
    loop = AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        AgentConfig(max_steps=6),
        model_client=model,
        registry=registry,
        understanding_service=understanding_service,
    )
    clarification = SimpleNamespace(
        tool_call_id=None,
        resume_kind=AgentClarificationResumeKind.QUESTION_UNDERSTANDING.value,
        resume_payload={"operation": "set_dimension_role", "slot_name": "店铺"},
        answer={
            "selections": [{"label": "按店铺分组", "value": "group_by:店铺"}],
            "text": None,
        },
        question="请确认店铺维度的使用方式",
        options=[{"label": "按店铺分组", "value": "group_by:店铺"}],
    )

    events = list(loop.resume(run, record, clarification, "用户澄清回答：按店铺分组"))

    assert _event_types(events)[:2] == ["clarification-accepted", "question-understood"]
    assert not understanding_service.called
    assert not any(isinstance(message, ToolMessage) for message in model.calls[0])
    assert any(
        isinstance(message, HumanMessage) and message.content == "今天店铺的客户数"
        for message in model.calls[0]
    )
    updated = captured_state["question_understanding"]
    assert updated["validation"]["status"] == "valid"
    assert updated["intent"]["dimension_slots"][0]["role"] == "group_by"
    assert updated["rewritten_question"] == "今天店铺的客户数"
    assert run.derived_state["semantic_asset_ids"] == [272, 276]


def test_fold_messages_folds_old_tool_results_only():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content=""),
        ToolMessage(content="x" * 500, tool_call_id="a"),
        AIMessage(content=""),
        ToolMessage(content="y" * 500, tool_call_id="b"),
    ]
    fold_tool_messages(messages, max_chars=100, keep_recent=2)
    assert messages[2].content == FOLDED_PLACEHOLDER
    assert messages[4].content == "y" * 500  # 最近窗口不折叠
    assert messages[0].content == "q"  # 非工具消息不折叠


def test_system_prompt_injects_history_and_confirmed_understanding():
    prompt = build_system_prompt(
        datasource_id=5,
        oid=1,
        history_summary="- 问：上月 GMV\n  SQL：select 1\n  答（摘要）：100 万",
        question_understanding={
            "rewritten_question": "查询上月授信额度",
            "intent": {"metric_mentions": ["授信额度"]},
            "validation": {"status": "valid"},
        },
    )
    assert "最近对话" in prompt
    assert "上月 GMV" in prompt
    assert "已确认的问题理解" in prompt
    assert "查询上月授信额度" in prompt
    assert "不得在工具规划阶段再次继承" in prompt


def test_system_prompt_omits_optional_sections():
    prompt = build_system_prompt(datasource_id=5, oid=1)
    assert "最近对话" not in prompt
    assert "## 已确认的问题理解" not in prompt
