import pytest

from apps.chatbi.orchestration.graph.capabilities.adapters.answer import AnswerAdapter
from apps.chatbi.orchestration.graph.capabilities.adapters.question import (
    QuestionAdapter,
    build_question_classification_prompt,
)
from apps.chatbi.orchestration.graph.capabilities.placeholder import (
    PlaceholderChatBICapabilityGateway,
)
from apps.chatbi.orchestration.graph.capabilities.real import (
    RealChatBICapabilityGateway,
)


class FakeModelClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FailingKnowledgeAdapter:
    def retrieve(self, request):
        raise RuntimeError("semantic failed")


class FakeSqlAdapter:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return {
            "sql": "select sum(visit_uv) as visit_uv from stall_traffic_1d",
            "strategy": "semantic_sql_compiler",
            "explanation": "fake sql",
            "used_assets": [],
        }


def _v1_request(question: str, conversation: dict | None = None, dataset_id: int | None = 7001) -> dict:
    request = {"question": question, "tenant_id": 9501, "user_id": 501}
    if dataset_id is not None:
        request["dataset_id"] = dataset_id
    return {
        "request": request,
        "conversation": conversation or {},
        "variables": {},
        "inputs": {},
        "node_name": "classify_question",
    }


def test_question_classification_prompt_constrains_model_to_stable_json():
    prompt = build_question_classification_prompt(
        question="最近 7 天销售额",
        dataset_id=7001,
        conversation_context={"last_question": "昨天销售额"},
    )

    assert "只做问题分类" in prompt.system_prompt
    assert "不要回答问题" in prompt.system_prompt
    assert "不要生成 SQL" in prompt.system_prompt
    assert '"category"' in prompt.system_prompt
    assert "forbidden" in prompt.system_prompt
    assert "chitchat" in prompt.system_prompt
    assert "data" in prompt.system_prompt
    assert "followup" in prompt.system_prompt
    assert "最近 7 天销售额" in prompt.user_prompt
    assert "dataset_id" in prompt.user_prompt
    assert "last_question" in prompt.user_prompt


def test_question_adapter_uses_model_json_classification():
    client = FakeModelClient(
        '{"category":"followup","reason":"依赖上一轮问题继续追问","risk_level":"low","confidence":0.91}'
    )
    adapter = QuestionAdapter(model_client=client)

    result = adapter.classify(_v1_request("那上个月呢", conversation={"last_question": "这个月销售额"}))

    assert result == {
        "category": "followup",
        "reason": "依赖上一轮问题继续追问",
        "risk_level": "low",
        "confidence": 0.91,
    }
    assert client.prompts


def test_question_adapter_extracts_json_from_markdown_response():
    client = FakeModelClient(
        """
        下面是分类结果：
        ```json
        {"category":"chitchat","reason":"用户在问候","risk_level":"low","confidence":0.88}
        ```
        """
    )
    adapter = QuestionAdapter(model_client=client)

    result = adapter.classify(_v1_request("你好，你能做什么"))

    assert result["category"] == "chitchat"
    assert result["reason"] == "用户在问候"
    assert result["confidence"] == 0.88


def test_question_rewrite_prompt_includes_dataset_id_and_ignores_false_missing_dataset():
    client = FakeModelClient(
        '{"rewritten_question":"今日店铺流量","need_user_input":true,'
        '"missing_slots":["dataset_id"],"image_profile_hint":null}'
    )
    adapter = QuestionAdapter(model_client=client)

    result = adapter.rewrite(_v1_request("今日店铺流量", dataset_id=3))

    assert result == {
        "rewritten_question": "今日店铺流量",
        "need_user_input": False,
        "missing_slots": [],
        "image_profile_hint": None,
    }
    assert '"dataset_id": 3' in client.prompts[0].user_prompt


@pytest.mark.parametrize(
    "response",
    [
        '{"category":"other","reason":"非法类别","risk_level":"low","confidence":0.7}',
        "不是 JSON",
    ],
)
def test_question_adapter_raises_when_classification_model_output_is_invalid(response: str):
    adapter = QuestionAdapter(model_client=FakeModelClient(response))

    with pytest.raises(ValueError, match="CLASSIFICATION_MODEL_OUTPUT_INVALID"):
        adapter.classify(_v1_request("最近 7 天销售额"))


def test_question_adapter_raises_when_classification_model_call_fails():
    adapter = QuestionAdapter(model_client=FakeModelClient(RuntimeError("model unavailable")))

    with pytest.raises(RuntimeError, match="CLASSIFICATION_MODEL_CALL_FAILED"):
        adapter.classify(_v1_request("最近 7 天销售额"))


def test_real_gateway_uses_real_adapters_and_routes_sql_generate_to_sql_adapter():
    question_client = FakeModelClient(
        '{"category":"chitchat","reason":"模型判断为闲聊","risk_level":"low","confidence":0.93}'
    )
    answer_client = FakeModelClient(
        '{"answer":"模型闲聊回复","warnings":[],"render_type":"text","citations":[]}'
    )
    sql_adapter = FakeSqlAdapter()
    gateway = RealChatBICapabilityGateway(
        question_adapter=QuestionAdapter(model_client=question_client),
        answer_adapter=AnswerAdapter(model_client=answer_client),
        sql_adapter=sql_adapter,
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )

    classification = gateway.invoke("question.classify", _v1_request("你好"), "run:classify")
    answer = gateway.invoke("answer.chitchat", _v1_request("你好"), "run:chitchat")
    sql = gateway.invoke("sql.generate", _v1_request("你好"), "run:sql")

    assert classification["category"] == "chitchat"
    assert classification["reason"] == "模型判断为闲聊"
    assert answer["answer"] == "模型闲聊回复"
    assert sql["strategy"] == "semantic_sql_compiler"
    assert sql["sql"] == "select sum(visit_uv) as visit_uv from stall_traffic_1d"
    assert sql_adapter.requests


def test_real_gateway_does_not_fallback_when_real_knowledge_node_fails():
    gateway = RealChatBICapabilityGateway(
        knowledge_adapter=FailingKnowledgeAdapter(),
        fallback_gateway=PlaceholderChatBICapabilityGateway(),
    )

    with pytest.raises(RuntimeError, match="semantic failed"):
        gateway.invoke("knowledge.retrieve", _v1_request("今日访问人数", dataset_id=3), "run:knowledge")
