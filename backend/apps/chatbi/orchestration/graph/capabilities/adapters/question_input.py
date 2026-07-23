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
    user_feedback: dict[str, Any] | None = None,
) -> QuestionClassificationPrompt:
    """构造问题重写节点的稳定 JSON 提示词。"""

    system_prompt = """
你是 ChatBI 工作流中的问题重写器，只做问题重写和语义保真规范化，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "rewritten_question": "补全上下文后的用户问题",
  "need_user_input": false,
  "missing_slots": [],
  "image_profile_hint": null
}

{shared_rewrite_rules}

- 如果可以判断适合的展示类型，可在 image_profile_hint 中给出 table、line、bar、pie 等简短提示；不确定时返回 null。

ChatBI 必需信息判定：
- dataset_id：语义数据集必须已经存在；如果 user_payload.dataset_id 不为空，说明上游已提供语义数据集，禁止把 dataset_id 放入 missing_slots；只有 user_payload.dataset_id 为空时才允许缺失槽位使用 dataset_id。
- metric 或 analysis_object：必须知道用户要分析什么指标、事实或业务对象，例如销售额、订单数、访问量、用户数、利润、客户、商品、订单。若问题只有“看一下情况”“分析一下”“怎么样”且上下文无法补全，need_user_input=true，missing_slots 包含 metric 或 analysis_object。
- time_range：默认不要因为缺少时间范围而澄清；很多业务问题可以先按系统默认时间或全量口径继续执行。只有用户明确要求趋势、对比、环比、同比、排行、按维度拆解，且缺失必要时间边界会导致问题不可执行或口径明显错误时，才把 time_range 放入 missing_slots。
- dimension：只有用户明确要求“按...看”“分...统计”“排行”“TopN”“对比不同...”但没有说明维度，且上下文无法补全时，才把 dimension 放入 missing_slots。
- filter：只有用户提到模糊对象或条件，例如“这个地区”“那个渠道”“这些客户”，且上下文无法解析时，才把 filter 放入 missing_slots。
- 不要为了追求完整而过度澄清；只要可以形成一个合理、可执行的数据问题，就应 need_user_input=false。
""".strip().replace(
        "{shared_rewrite_rules}",
        QUESTION_REWRITE_BUSINESS_RULES,
    )
    user_payload = {
        "question": question,
        "dataset_id": dataset_id,
        "conversation_context": conversation_context or {},
        "user_feedback": user_feedback or {},
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

