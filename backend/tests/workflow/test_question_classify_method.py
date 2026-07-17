import pytest

from apps.workflow.capabilities.adapters.question import QuestionAdapter


class RecordingModelClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _request(question: str, dataset_id: int | None = 3, conversation: dict | None = None) -> dict:
    request = {
        "question": question,
        "tenant_id": 1,
        "user_id": 1,
    }
    if dataset_id is not None:
        request["dataset_id"] = dataset_id
    return {
        "request": request,
        "conversation": conversation or {},
        "variables": {},
        "inputs": {},
        "node_name": "classify_question",
    }


@pytest.mark.parametrize(
    ("question", "model_response", "expected_category", "expected_reason"),
    [
        (
            "你好",
            '{"category":"chitchat","reason":"用户问候","risk_level":"low","confidence":0.96}',
            "chitchat",
            "用户问候",
        ),
        (
            "今日店铺流量",
            '{"category":"data","reason":"用户查询数据指标","risk_level":"low","confidence":0.92}',
            "data",
            "用户查询数据指标",
        ),
    ],
)
def test_classify_returns_model_classification(question, model_response, expected_category, expected_reason):
    client = RecordingModelClient(model_response)
    adapter = QuestionAdapter(model_client=client)

    result = adapter.classify(_request(question, conversation={"last_question": "昨日店铺流量"}))

    assert result == {
        "category": expected_category,
        "reason": expected_reason,
        "risk_level": "low",
        "confidence": pytest.approx(0.96 if expected_category == "chitchat" else 0.92),
    }
    assert len(client.prompts) == 1
    assert question in client.prompts[0].user_prompt
    assert "dataset_id" in client.prompts[0].user_prompt
    assert "last_question" in client.prompts[0].user_prompt


def test_classify_returns_forbidden_without_calling_model_when_question_is_empty():
    client = RecordingModelClient('{"category":"data","reason":"不应调用","risk_level":"low","confidence":0.9}')
    adapter = QuestionAdapter(model_client=client)

    result = adapter.classify(_request("   "))

    assert result == {
        "category": "forbidden",
        "reason": "empty_question",
        "risk_level": "medium",
        "confidence": 1.0,
    }
    assert client.prompts == []


def test_classify_returns_forbidden_without_calling_model_when_dataset_is_missing():
    client = RecordingModelClient('{"category":"data","reason":"不应调用","risk_level":"low","confidence":0.9}')
    adapter = QuestionAdapter(model_client=client)

    result = adapter.classify(_request("今日店铺流量", dataset_id=None))

    assert result == {
        "category": "forbidden",
        "reason": "missing_dataset",
        "risk_level": "medium",
        "confidence": 1.0,
    }
    assert client.prompts == []


def test_classify_raises_and_does_not_fallback_when_model_call_fails():
    adapter = QuestionAdapter(model_client=RecordingModelClient(RuntimeError("model unavailable")))

    with pytest.raises(RuntimeError, match="CLASSIFICATION_MODEL_CALL_FAILED"):
        adapter.classify(_request("你好"))


@pytest.mark.parametrize(
    "model_response",
    [
        "不是 JSON",
        '{"category":"data","reason":"置信度越界","risk_level":"low","confidence":1.5}',
        '{"category":"unknown","reason":"非法分类","risk_level":"low","confidence":0.9}',
    ],
)
def test_classify_raises_and_does_not_fallback_when_model_output_is_invalid(model_response):
    adapter = QuestionAdapter(model_client=RecordingModelClient(model_response))

    with pytest.raises(ValueError, match="CLASSIFICATION_MODEL_OUTPUT_INVALID"):
        adapter.classify(_request("今日店铺流量"))
