"""Graph 问题分类与问题重写节点的提示词。"""

import json
from typing import Any

from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    QuestionClassificationPrompt,
)
from apps.chatbi.services.understanding.prompts import (
    QUESTION_REWRITE_BUSINESS_RULES,
)


def build_question_classification_prompt(
    question: str,
    dataset_id: int | None,
    conversation_context: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造稳定 JSON 输出的分类提示词。"""

    context = conversation_context or {}
    system_prompt = """
你是 ChatBI 工作流中的问题分类器，只做问题分类，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "category": "forbidden | chitchat | data | followup",
  "reason": "不超过 40 个中文字符的分类原因",
  "risk_level": "low | medium | high",
  "confidence": 0.0
}

分类标准：
- forbidden：越权、绕过权限、危险操作、请求访问无授权数据、明显不应进入问数链路的问题。
- chitchat：问候、闲聊、能力咨询、非业务数据分析问题。
- data：完整的业务数据分析、统计、查询、趋势、排名、对比、归因问题。
- followup：依赖上文才能理解的追问，例如“那上个月呢”“按地区看一下”“继续分析利润”。

稳定性要求：
- category 只能取 forbidden、chitchat、data、followup。
- risk_level 只能取 low、medium、high。
- confidence 必须是 0 到 1 之间的数字。
- 不确定但像业务数据问题时，优先选择 data。
- 不确定但明显依赖上文时，优先选择 followup。
""".strip()
    user_payload = {
        "question": question,
        "dataset_id": dataset_id,
        "conversation_context": context,
    }
    user_prompt = "请分类以下 ChatBI 用户输入，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def build_question_rewrite_prompt(
    question: str,
    dataset_id: int | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造问题重写节点的稳定 JSON 提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的问题重写模型。你只做问题重写和指标、维度短语识别，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "original_question": "用户本轮提交的原始问题",
  "rewrite_question": "补全上下文后的完整问题",
  "metric_phrases": [],
  "dimension_phrases": []
}

{shared_rewrite_rules}

- metric_phrases 和 dimension_phrases 必须根据 rewrite_question 的自然语言含义识别，不得按字符位置、固定句式或固定词表截取。
- 时间表达、维度值、疑问词、排序词和计算关系不进入两个短语列表。
""".strip().replace(
        "{shared_rewrite_rules}",
        QUESTION_REWRITE_BUSINESS_RULES,
    )
    user_payload = {
        "question": question,
        "dataset_id": dataset_id,
        "conversation_context": conversation_context or {},
    }
    user_prompt = "请重写以下 ChatBI 用户输入，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
