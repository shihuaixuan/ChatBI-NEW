import pytest

from apps.chatbi.models import (
    AnswerGenerationData,
    AnswerGenerationPrompt,
    QuestionModelResponse,
)
from apps.chatbi.services.generation import (
    AnswerGenerationService,
    CallableAnswerModelClient,
    build_answer_generation_prompt,
)
from apps.chatbi.services.understanding import StructuredModelService


class FakeQuestionModelClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts: list[tuple[str, str]] = []

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        self.prompts.append((system_prompt, user_prompt))
        if isinstance(self.response, Exception):
            raise self.response
        return QuestionModelResponse(content=self.response)


def _service(response: str | Exception) -> AnswerGenerationService:
    return AnswerGenerationService(
        StructuredModelService(FakeQuestionModelClient(response))
    )


def test_answer_generation_prompt_keeps_safe_structured_contract():
    prompt = build_answer_generation_prompt(
        mode="generate",
        question="今日访问量",
        projection={"execution": {"status": "succeeded"}},
    )

    assert "只生成用户可读回复" in prompt.system_prompt
    assert "不要泄露内部变量" in prompt.system_prompt
    assert '"answer"' in prompt.system_prompt
    assert "今日访问量" in prompt.user_prompt
    assert '"execution"' in prompt.user_prompt


def test_answer_generation_accepts_wrapped_json_object():
    result = _service(
        '回答如下：```json\n{"answer":"今日访问量为 123。","warnings":[],'
        '"render_type":"text","citations":[]}\n```'
    ).generate(
        AnswerGenerationData(
            mode="generate",
            question="今日访问量",
            projection={"execution": {"row_count": 1}},
        )
    )

    assert result.model_dump(mode="json") == {
        "answer": "今日访问量为 123。",
        "warnings": [],
        "render_type": "text",
        "citations": [],
    }


@pytest.mark.parametrize(
    "response",
    [
        "不是 JSON",
        '{"answer":"缺少字段","warnings":"invalid"}',
    ],
)
def test_answer_generation_uses_parse_fallback_for_invalid_output(response: str):
    result = _service(response).generate(
        AnswerGenerationData(
            mode="generate",
            question="今日访问量",
            projection={},
        )
    )

    assert result.answer == "暂时无法生成完整回答，请稍后重试。"
    assert result.warnings == ["answer_generation_parse_failed"]


def test_answer_generation_uses_model_fallback_for_call_failure():
    result = _service(RuntimeError("model unavailable")).generate(
        AnswerGenerationData(
            mode="generate",
            question="今日访问量",
            projection={},
        )
    )

    assert result.answer == "暂时无法生成完整回答，请稍后重试。"
    assert result.warnings == ["answer_generation_model_failed"]


def test_answer_generation_degraded_fallback_keeps_node_error_code():
    result = _service(RuntimeError("model unavailable")).generate(
        AnswerGenerationData(
            mode="generate",
            question="今日访问量",
            projection={
                "node_failure": {"error_code": "SQL_GENERATE_FAILED"}
            },
        )
    )

    assert "SQL_GENERATE_FAILED" in result.answer
    assert result.warnings == ["answer_generation_degraded"]


@pytest.mark.parametrize(
    ("mode", "answer", "warning"),
    [
        (
            "reject",
            "当前问题无法在权限范围内回答。",
            "answer_reject_model_failed",
        ),
        (
            "chitchat",
            "你好，我可以帮你分析业务数据问题。",
            "answer_chitchat_model_failed",
        ),
    ],
)
def test_answer_generation_keeps_mode_specific_safe_fallbacks(
    mode: str,
    answer: str,
    warning: str,
):
    result = _service(RuntimeError("model unavailable")).generate(
        AnswerGenerationData(
            mode=mode,
            question="测试问题",
            projection={},
        )
    )

    assert result.answer == answer
    assert result.warnings == [warning]


def test_callable_answer_model_client_preserves_legacy_prompt_port():
    prompts: list[AnswerGenerationPrompt] = []

    def client(prompt: AnswerGenerationPrompt) -> str:
        prompts.append(prompt)
        return '{"answer":"成功","warnings":[],"render_type":"text","citations":[]}'

    response = CallableAnswerModelClient(client).invoke("系统提示", "用户提示")

    assert response.content.startswith('{"answer"')
    assert prompts == [
        AnswerGenerationPrompt(
            system_prompt="系统提示",
            user_prompt="用户提示",
        )
    ]
