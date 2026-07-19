from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GenerationCustomPromptType(str, Enum):
    GENERATE_SQL = "GENERATE_SQL"
    ANALYSIS = "ANALYSIS"
    PREDICT_DATA = "PREDICT_DATA"


@dataclass(frozen=True, slots=True)
class GenerationCustomPromptQuery:
    """生成流程查询自定义提示词所需的稳定输入。"""

    prompt_type: GenerationCustomPromptType
    workspace_id: int | None
    datasource_id: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationCustomPromptResult:
    """自定义提示词文本及日志展示项。"""

    prompt: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)


__all__ = [
    "GenerationCustomPromptQuery",
    "GenerationCustomPromptResult",
    "GenerationCustomPromptType",
]
