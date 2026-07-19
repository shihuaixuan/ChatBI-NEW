from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SemanticRetrievalData:
    """ChatBI 发起语义资产检索所需的稳定输入。"""

    workspace_id: int
    user_id: int | None
    dataset_id: int
    original_question: str
    rewritten_question: str
    intent: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


__all__ = ["SemanticRetrievalData"]
