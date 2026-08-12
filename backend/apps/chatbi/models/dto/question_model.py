from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class QuestionModelJSONMode(str, Enum):
    """问题理解模型 JSON 响应解析方式。"""

    STRICT = "strict"
    EXTRACT_OBJECT = "extract_object"


@dataclass(frozen=True, slots=True)
class QuestionModelInvocationData:
    """问题理解各阶段共享的模型调用输入。"""

    stage: str
    system_prompt: str
    user_prompt: str
    json_mode: QuestionModelJSONMode = QuestionModelJSONMode.STRICT


@dataclass(frozen=True, slots=True)
class QuestionModelResponse:
    """模型客户端返回的原始正文和用量。"""

    content: str
    usage_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionModelResult:
    """完成 JSON 对象解析后的统一模型结果。"""

    payload: dict[str, Any]
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    raw_content: str = ""


__all__ = [
    "QuestionModelInvocationData",
    "QuestionModelJSONMode",
    "QuestionModelResponse",
    "QuestionModelResult",
]
