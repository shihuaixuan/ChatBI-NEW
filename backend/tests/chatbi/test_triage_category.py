"""P0-2 分诊 category：chitchat/meta_query/out_of_scope 各自收口正确。"""

from __future__ import annotations

from types import SimpleNamespace

from apps.chatbi.models.dto.question_understanding import (
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionUnderstandingOutput,
)
from apps.chatbi.orchestration.agent.loop import _allows_direct_answer
from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.state import AgentRuntimeState
from apps.chatbi.orchestration.agent.tools.base import AgentToolContext
from apps.chatbi.services.generation.capability_answer import build_capability_answer
from apps.chatbi.services.understanding.understanding_service import (
    _normalize_question_category,
)
from apps.semantic.models.dto.dataset_schema import DatasetSchema, SchemaElement
from apps.tool import BudgetGuard


def _output_with_category(category: str) -> QuestionUnderstandingOutput:
    return QuestionUnderstandingOutput(
        original_question="你好",
        rewrite_question="你好",
        metric_phrases=[],
        dimension_phrases=[],
        intent=IntentRecognitionOutput(intent_type="unknown", confidence=0.9),
        validation=IntentValidationOutput(status="valid"),
        category=category,
    )


def _state_with_category(category: str) -> AgentRuntimeState:
    state = AgentRuntimeState(
        run=SimpleNamespace(id=1, oid=1, chat_id=1),
        record=SimpleNamespace(id=1),
        context=AgentToolContext(session=None, oid=1, user_id=1, datasource_id=1),
        messages=[AgentMessage.user("你好")],
        budget=BudgetGuard(max_steps=5),
        system=AgentMessage.system("系统提示词"),
    )
    state.context.state["question_understanding"] = (
        _output_with_category(category).model_dump(mode="json")
    )
    return state


def test_category_defaults_to_data_query_for_old_snapshots():
    output = QuestionUnderstandingOutput(
        original_question="q",
        rewrite_question="q",
        metric_phrases=[],
        dimension_phrases=[],
        intent=IntentRecognitionOutput(intent_type="metric_query", confidence=0.9),
        validation=IntentValidationOutput(status="valid"),
    )
    assert output.category == "data_query"
    # 旧持久化快照（无 category 字段）可正常反序列化。
    payload = output.model_dump(mode="json")
    payload.pop("category")
    restored = QuestionUnderstandingOutput.model_validate(payload)
    assert restored.category == "data_query"


def test_direct_answer_only_allowed_for_chitchat(monkeypatch):
    monkeypatch.setattr(
        "apps.chatbi.orchestration.agent.loop.settings.CHATBI_TRIAGE_ENABLED",
        True,
    )
    assert _allows_direct_answer(_state_with_category("chitchat")) is True
    assert _allows_direct_answer(_state_with_category("data_query")) is False
    assert _allows_direct_answer(_state_with_category("meta_query")) is False
    assert _allows_direct_answer(_state_with_category("out_of_scope")) is False


def test_triage_switch_can_restore_legacy_behavior(monkeypatch):
    monkeypatch.setattr(
        "apps.chatbi.orchestration.agent.loop.settings.CHATBI_TRIAGE_ENABLED",
        False,
    )

    assert _allows_direct_answer(_state_with_category("chitchat")) is False


def test_category_normalized_from_model_aliases():
    assert _normalize_question_category("chat") == "chitchat"
    assert _normalize_question_category("Meta") == "meta_query"
    assert _normalize_question_category("OUT_OF_SCOPE") == "out_of_scope"
    assert _normalize_question_category("什么乱码") == "data_query"
    assert _normalize_question_category(None) == "data_query"


def test_capability_answer_lists_dataset_assets():
    schema = DatasetSchema(
        data_set=SchemaElement(
            data_set_id=1,
            data_set_name="门店销售",
            id=1,
            name="门店销售",
            biz_name="门店销售",
            type="dataset",
        ),
        metrics=[
            SchemaElement(
                data_set_id=1,
                data_set_name="门店销售",
                id=11,
                name="销售额",
                biz_name="销售额",
                type="metric",
            ),
            SchemaElement(
                data_set_id=1,
                data_set_name="门店销售",
                id=12,
                name="订单数",
                biz_name="订单数",
                type="metric",
            ),
        ],
        dimensions=[
            SchemaElement(
                data_set_id=1,
                data_set_name="门店销售",
                id=21,
                name="门店",
                biz_name="门店",
                type="dimension",
            ),
        ],
    )
    answer = build_capability_answer(schema)
    assert "门店销售" in answer
    assert "销售额" in answer and "订单数" in answer
    assert "共 2 个" in answer
    assert "门店" in answer
    assert "示例问法" in answer


def test_capability_answer_without_schema_degrades_gracefully():
    answer = build_capability_answer(None)
    assert "暂未配置语义指标" in answer
    assert "示例问法" in answer
