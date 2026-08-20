"""Research Policy 提示词渲染。"""

from __future__ import annotations

import json
from typing import Any

from apps.chatbi.services.research.policy_rules import RESEARCH_POLICY_SYSTEM_RULES


class DefaultResearchPolicyPromptBuilder:
    """把受控研究上下文渲染为一次结构化模型调用。"""

    def build(self, context: dict[str, Any]) -> tuple[str, str]:
        return (
            RESEARCH_POLICY_SYSTEM_RULES,
            "请根据以下研究上下文返回 ResearchPolicyDecision：\n"
            + json.dumps(
                context,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        )


__all__ = ["DefaultResearchPolicyPromptBuilder"]
