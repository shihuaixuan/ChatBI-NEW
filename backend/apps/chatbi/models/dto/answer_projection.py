from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnswerProjectionData:
    """回答模型所需上下文的稳定输入。"""

    raw_question: str
    rewritten_question: str
    plan: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)
    node_failure: dict[str, Any] = field(default_factory=dict)
    sql_error: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnswerProjectionResult:
    """排除敏感执行细节后的回答模型上下文。"""

    payload: dict[str, Any]


__all__ = ["AnswerProjectionData", "AnswerProjectionResult"]
