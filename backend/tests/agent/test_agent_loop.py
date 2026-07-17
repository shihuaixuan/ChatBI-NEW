"""AgentLoop 端到端行为测试：FakeSession + 脚本化模型客户端，不依赖真实 DB/LLM。"""

from types import SimpleNamespace

import orjson
import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from apps.chat.models.chat_model import ChatRecord
from apps.agent.loop import AgentLoop
from apps.agent.models import (
    AgentRunStatus,
    ChatbiAgentRun,
    ChatbiAgentTraceEvent,
)
from apps.agent.schemas import AgentConfig
from apps.agent.tools.base import AgentTool, ToolOutput
from apps.agent.tools.registry import ToolRegistry
from apps.capabilities.question_understanding import (
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionUnderstandingError,
    QuestionUnderstandingModelResponse,
    QuestionUnderstandingOutcome,
    QuestionUnderstandingOutput,
    QuestionUnderstandingService,
)


class FakeSession:
    def __init__(self):
        self.trace_count = 0
        self.added = []

    def add(self, obj):
        self.added.append(obj)
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


class SearchSemanticAssetsProbeArgs(BaseModel):
    pass


class ProbeTool(AgentTool):
    name = "probe"
    description = "probe"
    args_model = ProbeArgs

    def execute(self, ctx, args):
        ctx.state["last_execution"] = {"sql": "select 1", "fields": ["a"], "row_count": 1, "sql_source": "compiled"}
        ctx.state["full_data"] = [{"a": 1}]
        return ToolOutput(success=True, summary="probed", payload={"value": args.value})


class SearchSemanticAssetsProbeTool(AgentTool):
    """模拟从运行状态读取意图的无参语义检索工具。"""

    name = "search_semantic_assets"
    description = "search semantic assets"
    args_model = SearchSemanticAssetsProbeArgs

    def execute(self, ctx, args):
        return ToolOutput(
            success=True,
            summary="searched",
            payload={"status": "hit", "metrics": ["gmv"], "dimensions": [], "tables": []},
        )


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
    return AgentLoop(
        FakeSession(),
        SimpleNamespace(id=1, oid=1),
        config or AgentConfig(max_steps=5),
        model_client=model,
        registry=_registry(),
        understanding_service=StaticUnderstandingService(),
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

    assert types[:3] == ["record-created", "run-started", "question-understood"]
    assert "tool-called" in types and "tool-result" in types
    assert types[-3:] == ["answer", "run-finished", "finish"]
    assert run.status == AgentRunStatus.FINISHED.value
    assert record.sql_answer == "最终答案"
    assert record.sql == "select 1"
    assert orjson.loads(record.data) == {"fields": ["a"], "data": [{"a": 1}]}
    # 消息历史持久化：human + 2 轮 assistant + 2 条 tool 回写
    assert len(run.messages) == 5
    assert run.derived_state["question_understanding"]["intent"]["metric_mentions"] == ["gmv"]
    assert run.budget_snapshot["tokens_used"] == 17
    assert "时间筛选必须原样使用 `time_range.normalized`" in model.calls[0][0].content


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
        "apps.agent.loop.crud.recent_qa_summaries",
        lambda session, chat_id, exclude_record_id, limit: [
            {
                "question": "今天店铺的客户数",
                "sql": "select previous_month",
                "answer_brief": "上个月各店铺的销售下单客户数",
            }
        ],
    )
    monkeypatch.setattr(
        "apps.agent.loop.crud.latest_successful_rewritten_question",
        lambda session, **kwargs: "今天按店铺分组的销售下单客户数",
    )

    model = ScriptedModel([AIMessage(content="完成")])
    run, record = _run_and_record()
    loop = AgentLoop(
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

    events = [orjson.loads(item.removeprefix("data:")) for item in _loop(model).run(run, record)]

    tool_event = next(item for item in events if item["type"] == "tool-called")
    assert tool_event["content"]["args_summary"]["rewritten_question"] == "按城市看 gmv"
    assert tool_event["content"]["args_summary"]["intent"]["metric_mentions"] == ["gmv"]


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
    list(_loop(model, AgentConfig(max_steps=10, repeat_fuse_threshold=3)).run(run, record))

    assert run.status == AgentRunStatus.FAILED.value
    assert run.error_class == "budget_exhausted"
    assert "重复熔断" in run.error


def test_unknown_tool_is_rejected_but_loop_continues():
    model = ScriptedModel([
        _tool_message("hack_tool", {"x": 1}),
        AIMessage(content="好的，我换个方式直接回答。"),
    ])
    run, record = _run_and_record()
    list(_loop(model).run(run, record))

    assert run.status == AgentRunStatus.FINISHED.value
    # 第二轮的消息历史里包含拒绝回写
    second_call_messages = model.calls[1]
    tool_messages = [m for m in second_call_messages if m.__class__.__name__ == "ToolMessage"]
    assert any("不在白名单" in m.content for m in tool_messages)


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
