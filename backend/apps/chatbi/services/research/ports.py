"""Research 子域的可替换模型提示词端口。"""

from __future__ import annotations

from typing import Any, Protocol


class ResearchPolicyPromptBuilder(Protocol):
    """构造 Research Policy 单轮决策提示词。"""

    def build(self, context: dict[str, Any]) -> tuple[str, str]: ...


__all__ = ["ResearchPolicyPromptBuilder"]
