from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationData:
    """问题重写和自然语言意图的确定性校验输入。"""

    rewrite_need_user_input: bool = False
    rewrite_missing_slots: tuple[str, ...] = ()
    intent_type: str = "unknown"
    metric_mentions: tuple[str, ...] = ()
    dimension_slots: tuple[dict[str, Any], ...] = ()
    time_range: dict[str, Any] = field(default_factory=dict)
    ambiguous_slots: tuple[str, ...] = ()
    conflict_slots: tuple[str, ...] = ()
    subject_domain: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationIssue:
    """单个确定性问题，类别用于执行器映射到既有澄清节点。"""

    code: str
    category: Literal["rewrite", "intent", "slot", "repair"]
    clarification_slots: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionUnderstandingValidationResult:
    """与 Agent、Graph 展示契约无关的统一校验结果。"""

    issues: tuple[QuestionUnderstandingValidationIssue, ...] = ()

    @property
    def reason_codes(self) -> list[str]:
        return _unique_strings([issue.code for issue in self.issues])

    @property
    def clarification_slots(self) -> list[str]:
        return _unique_strings(
            [slot for issue in self.issues for slot in issue.clarification_slots]
        )


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "QuestionUnderstandingValidationData",
    "QuestionUnderstandingValidationIssue",
    "QuestionUnderstandingValidationResult",
]
