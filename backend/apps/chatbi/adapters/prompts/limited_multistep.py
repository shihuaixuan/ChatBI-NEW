"""受限多步执行需求分解提示词渲染。"""

from __future__ import annotations

import json
from typing import Any

from apps.chatbi.services.planning.limited_multistep_rules import (
    LIMITED_MULTISTEP_SYSTEM_RULES,
)


class DefaultLimitedMultiStepPromptBuilder:
    """使用固定规则渲染受限多步结构化提示词。"""

    def build(self, context: dict[str, Any]) -> tuple[str, str]:
        """渲染首次分解调用的系统提示词和用户输入。"""

        return (
            LIMITED_MULTISTEP_SYSTEM_RULES,
            "请根据以下受限上下文生成 ExecutionRequirementDraft：\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True),
        )

    def build_repair(
        self,
        context: dict[str, Any],
        draft_payload: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """渲染唯一一次结构化修复调用，不允许改变原分析目标。"""

        return (
            LIMITED_MULTISTEP_SYSTEM_RULES,
            "上一次草案未通过确定性校验。只能修复列出的结构错误，不得改变分析目标。\n"
            + json.dumps(
                {
                    "context": context,
                    "previous_draft": draft_payload,
                    "validation_errors": errors,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        )


__all__ = ["DefaultLimitedMultiStepPromptBuilder"]
