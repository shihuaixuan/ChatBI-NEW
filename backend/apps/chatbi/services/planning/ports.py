"""规划子域的可替换技术端口。"""

from __future__ import annotations

from typing import Any, Protocol


class LimitedMultiStepPromptBuilder(Protocol):
    """构造受限多步首次分解和唯一一次修复提示词。"""

    def build(self, context: dict[str, Any]) -> tuple[str, str]: ...

    def build_repair(
        self,
        context: dict[str, Any],
        draft_payload: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> tuple[str, str]: ...


__all__ = ["LimitedMultiStepPromptBuilder"]
