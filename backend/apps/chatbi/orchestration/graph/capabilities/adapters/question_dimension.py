"""Graph 维度槽位提示词与候选规范化规则。"""

from typing import Any

from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    QuestionClassificationPrompt,
    markdown_user_prompt,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    normalize_dimension_candidates,
)
from apps.chatbi.services.understanding.prompts import DIMENSION_EXTRACTION_RULES


def build_dimension_slots_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造维度槽位识别提示词。"""

    dimension_candidates = normalize_dimension_candidates(
        available_dimensions or []
    )
    plain_dimension_candidates = [
        candidate
        for candidate in dimension_candidates
        if not candidate.get("is_time")
    ]
    time_dimension_candidates = [
        candidate
        for candidate in dimension_candidates
        if candidate.get("is_time")
    ]
    system_prompt = """
# 角色

你是 ChatBI 工作流中的维度槽位识别器，只负责识别维度、维度角色和维度值。

# 输出

只输出 JSON 对象。

```json
{
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "自然语言维度名", "role": "group_by | filter | display | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
  ],
  "residual_filter_mentions": [],
  "ambiguous_slots": [],
  "conflict_slots": []
}
```

# 维度候选规则

{shared_dimension_rules}

- 「可用维度」是普通维度候选，只作为理解用户维度表达的参考，不是输出白名单。
- 「时间字段候选」只用于后续时间字段绑定，不能输出到 `dimension_mentions` 或 `dimension_slots`。
- `dimension_mentions` 和 `dimension_slots[].name` 输出用户问题里的自然语言维度短语，例如“店铺”“商品”“地区”“渠道”。
- 维度识别必须独立完成，不要依赖指标线索识别子任务的输出，也不要假设其他子任务会纠正当前结果。
- 不要为了命中维度候选而拆分指标短语内部的业务修饰关系；当一个候选词只是修饰某个可度量业务结果时，应保留在原短语语义内，不要单独输出为维度槽位。
- 用户表达命中「可用维度」的 `name` 或 `aliases` 时，必须输出对应的标准 `name`。
- 用户表达没有完全命中时，应在「可用维度」中选择语义最相近、业务上最有关联的候选，并输出对应的标准 `name`。
- 如果多个候选都可能匹配，或用户表达和全部候选差异很大，不要强行替换；保留用户原文维度短语，并在 `ambiguous_slots` 中加入 `dimension`。
- 不输出维度 ID、字段名、`biz_name`。

# 维度值规则

- `dimension_slots[].value` 只填写维度值本身，不包含已命中的维度 `name` 或 `aliases`。
- 如果用户表达由「维度名或别名 + 值」组成，且维度名或别名已经用于确定维度，则 `value` 只保留剩余值部分。
- 如果用户表达由「值 + 维度名或别名」组成，且维度名或别名已经用于确定维度，则 `value` 只保留剩余值部分。
- 如果无法判断值边界，输出 `value=null`、`value_status=ambiguous`，并在 `ambiguous_slots` 中加入 `filter_value`。
- 时间表达不能作为普通维度值。

# 类型提示规则

- `value_kind=numeric_id`：值通常是编号、ID、数字代码；如果值部分是数字，保留数字字符串。
- `value_kind=string_label`：值通常是名称、标签、枚举文本；不要因为包含数字就只保留数字。
- `value_kind=enum`：值通常是枚举文本，保留用户表达的枚举值。
- `is_time=true`：该维度是时间字段候选，不承载“今天、本月、最近7天”等时间范围值，也不能作为普通维度槽位输出。

# residual_filter_mentions

只有当用户表达了筛选条件，但无法归属到任何「可用维度」时，才输出到 `residual_filter_mentions`。

已进入 `dimension_slots` 的筛选条件，不要重复输出到 `residual_filter_mentions`。
""".strip().replace(
        "{shared_dimension_rules}",
        DIMENSION_EXTRACTION_RULES,
    )
    user_prompt = markdown_user_prompt(
        rewritten_question=rewritten_question,
        available_dimensions=plain_dimension_candidates,
        time_dimensions=time_dimension_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请识别维度、维度角色和维度值，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
