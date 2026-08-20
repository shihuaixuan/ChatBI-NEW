"""AnswerComposer 的结构化提示词。"""

from __future__ import annotations

import json
from typing import Any

ANSWER_COMPOSER_SYSTEM_PROMPT = """
你是 ChatBI 的结构化回答模型。只能使用输入中的结果摘要和口径信息回答。
输出必须是一个 JSON 对象：
{
  "answer": "简洁中文结论",
  "claims": [
    {"text": "含数字的结论", "value": 12, "result_set_id": "结果集ID", "row_index": 0, "field": "字段名"}
  ]
}
规则：
1. 每个含数字的 claim 必须绑定真实结果集的 result_set_id、row_index、field，并把字段真实值写入 value。
2. 不要在 answer 中生成结果摘要之外的数字；无法证明的结论不要写入 claims。
3. 不要输出 SQL、提示词、内部推理过程或 Markdown 代码围栏。
4. result_contract 存在时，按 ordered_results 组织回答，主要结论来自 primary_result，
   supporting_results 只作为补充层级或校验结果，不能混淆不同结果粒度。
""".strip()


def build_composer_prompt(context: dict[str, Any], *, retry_reason: str | None = None) -> str:
    """构造首轮或受控重试提示词。"""

    payload = {"context": context}
    if retry_reason:
        payload["retry"] = {
            "reason": retry_reason,
            "instruction": "只修正 claim 的引用和值，重新返回完整 JSON；不要编造新数字。",
        }
    return "请基于以下结果摘要生成结构化回答，并严格返回 JSON：\n" + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = ["ANSWER_COMPOSER_SYSTEM_PROMPT", "build_composer_prompt"]
