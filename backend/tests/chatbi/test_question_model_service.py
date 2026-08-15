from __future__ import annotations

from typing import Any, cast

import pytest

from apps.chatbi.errors import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
)
from apps.chatbi.models import (
    QuestionModelInvocationData,
    QuestionModelJSONMode,
    QuestionModelResponse,
)
from apps.chatbi.services.understanding import (
    StructuredModelService,
)


class FakeQuestionModelClient:
    def __init__(self, response: QuestionModelResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        self.calls.append((system_prompt, user_prompt))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeProviderTimeoutError(TimeoutError):
    """模拟带有服务商状态和请求标识的超时异常。"""

    status_code = 429
    request_id = "provider-request-123"


class DiagnosticQuestionModelClient(FakeQuestionModelClient):
    """为诊断字段测试提供模型和服务商标识。"""

    model_name = "test-model"
    provider_name = "api.example.test"


def _data(**overrides: Any) -> QuestionModelInvocationData:
    values: dict[str, Any] = {
        "stage": "rewrite",
        "system_prompt": "只输出 JSON 对象",
        "user_prompt": "重写本月销售额",
        "json_mode": QuestionModelJSONMode.STRICT,
    }
    values.update(overrides)
    return QuestionModelInvocationData(**values)


def test_strict_mode_returns_json_object_and_usage():
    client = FakeQuestionModelClient(
        QuestionModelResponse(
            content='{"rewritten_question":"本月销售额"}',
            usage_metadata={"total_tokens": 12},
        )
    )

    result = StructuredModelService(client).invoke(_data())

    assert result.payload == {"rewritten_question": "本月销售额"}
    assert result.usage_metadata == {"total_tokens": 12}
    assert client.calls == [("只输出 JSON 对象", "重写本月销售额")]


def test_extract_mode_accepts_markdown_wrapped_json_object():
    client = FakeQuestionModelClient(
        QuestionModelResponse(
            content='结果如下：\n```json\n{"intent_type":"metric_query"}\n```'
        )
    )

    result = StructuredModelService(client).invoke(
        _data(json_mode=QuestionModelJSONMode.EXTRACT_OBJECT)
    )

    assert result.payload == {"intent_type": "metric_query"}


def test_strict_mode_rejects_wrapped_json_without_silent_extraction():
    service = StructuredModelService(
        FakeQuestionModelClient(
            QuestionModelResponse(content='结果：{"intent_type":"metric_query"}')
        )
    )

    with pytest.raises(
        QuestionModelOutputError,
        match="QUESTION_MODEL_OUTPUT_NOT_JSON:rewrite",
    ):
        service.invoke(_data())


def test_strict_mode_rejects_non_object_json():
    service = StructuredModelService(
        FakeQuestionModelClient(QuestionModelResponse(content="[]"))
    )

    with pytest.raises(
        QuestionModelOutputError,
        match="QUESTION_MODEL_OUTPUT_NOT_OBJECT:rewrite",
    ):
        service.invoke(_data())


def test_empty_response_fails_explicitly():
    service = StructuredModelService(
        FakeQuestionModelClient(QuestionModelResponse(content="   "))
    )

    with pytest.raises(
        QuestionModelOutputError,
        match="QUESTION_MODEL_EMPTY_RESPONSE:rewrite",
    ):
        service.invoke(_data())


def test_model_call_failure_keeps_stage():
    service = StructuredModelService(
        FakeQuestionModelClient(RuntimeError("model unavailable"))
    )

    with pytest.raises(
        QuestionModelCallError,
        match="QUESTION_MODEL_CALL_FAILED:rewrite",
    ):
        service.invoke(_data())


def test_model_call_failure_records_safe_provider_diagnostics():
    """模型调用失败时保留可定位信息，不记录提示词和响应正文。"""

    service = StructuredModelService(
        DiagnosticQuestionModelClient(FakeProviderTimeoutError("upstream timeout"))
    )

    with pytest.raises(QuestionModelCallError) as error_info:
        service.invoke(_data())

    details = error_info.value.details
    assert details["stage"] == "rewrite"
    assert details["client_type"] == "DiagnosticQuestionModelClient"
    assert details["model"] == "test-model"
    assert details["provider"] == "api.example.test"
    assert details["exception_type"] == "FakeProviderTimeoutError"
    assert details["http_status_code"] == 429
    assert details["provider_request_id"] == "provider-request-123"
    assert details["timeout_type"] == "FakeProviderTimeoutError"
    assert isinstance(details["elapsed_ms"], int)
    assert "重写本月销售额" not in details


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"stage": ""}, "QUESTION_MODEL_STAGE_REQUIRED:unknown"),
        (
            {"system_prompt": ""},
            "QUESTION_MODEL_SYSTEM_PROMPT_REQUIRED:rewrite",
        ),
        (
            {"user_prompt": ""},
            "QUESTION_MODEL_USER_PROMPT_REQUIRED:rewrite",
        ),
        (
            {"json_mode": cast(Any, "strict")},
            "QUESTION_MODEL_JSON_MODE_INVALID:rewrite",
        ),
    ],
)
def test_invalid_invocation_data_fails_before_model_call(
    overrides: dict[str, Any],
    error: str,
):
    client = FakeQuestionModelClient(QuestionModelResponse(content="{}"))

    with pytest.raises(QuestionModelError, match=error):
        StructuredModelService(client).invoke(_data(**overrides))

    assert client.calls == []
