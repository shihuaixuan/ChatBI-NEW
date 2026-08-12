"""Graph 意图形态、指标线索与时间线索提示词。"""

import json
from typing import Any

from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    QuestionClassificationPrompt,
    markdown_user_prompt,
    normalize_subject_domain_candidates,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    normalize_dimension_candidates,
)
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
)


def build_intent_recognition_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造意图识别节点的稳定 JSON 提示词。"""

    domain_candidates = normalize_subject_domain_candidates(subject_domains or [])
    dimension_candidates = normalize_dimension_candidates(
        available_dimensions or []
    )
    system_prompt = """
你是 ChatBI 工作流中的意图识别器，只做意图识别，不要回答问题，不要生成 SQL，不要解释业务指标。

你必须只输出一个 JSON 对象，不能输出 Markdown、前后缀文本或多余说明。JSON 字段如下：
{
  "intent_type": "metric_query",
  "confidence": 0.0,
  "metric_mentions": [],
  "dimension_mentions": [],
  "dimension_slots": [],
  "time_mentions": [],
  "time_range": {"raw": null, "value_status": "not_provided"},
  "filter_mentions": [],
  "required_slot_types": [],
  "query_shape": {},
  "subject_domain": {"status": "not_required", "domain_id": null, "domain_name": null, "domain_biz_name": null, "confidence": 0.0, "reason": "", "candidate_domain_ids": []},
  "ambiguous_slots": [],
  "conflict_slots": []
}

可选 intent_type：
- metric_query：查询指标值或统计值，例如“今日访问量”“本月销售额”。
- trend_analysis：趋势、走势、按天、按周、按月变化。
- ranking_analysis：排行、最高、最低、TopN、前 N、后 N。
- comparison_analysis：同比、环比、对比、较上期、多个对象比较。
- detail_query：明细、详情、列表、清单。
- share_analysis：占比、构成、比例。
- anomaly_analysis：异常、波动、下降原因、为什么上升/下降。
- unknown：无法判断用户想做哪类分析。

检索线索要求：
- metric_mentions：用户原文或重写问题里疑似指标/事实/业务对象的自然语言短语，例如“销售额”“订单数”“访问人数”。不要输出 Semantic asset_id、biz_name 或数据库字段名。
- dimension_mentions：用户原文或重写问题里疑似分组、排行、对比、明细展示维度的自然语言短语，例如“商品”“地区”“渠道”。不要输出 Semantic asset_id。
- dimension_slots：对每个维度输出结构化槽位，格式为 {"name":"自然语言维度名","role":"group_by | filter | ambiguous","value":null,"value_status":"provided | not_provided | ambiguous"}。
  - “各档口/按档口/每个档口”表示 role=group_by，value=null，value_status=not_provided。
  - “1号档口/档口 1”表示 role=filter，value="1"，value_status=provided。
  - “档口的访问人数”这类没有明确“各/按/每个”且没有具体值的表达，role=ambiguous，value=null，value_status=not_provided。
- 如果 user_payload.available_dimensions 非空，dimension_mentions 和 dimension_slots[].name 只能从 user_payload.available_dimensions 的 name 或 aliases 中选择；不在候选维度中的词不能输出为维度。
- 线上/线下/新增/活跃/累计 等词如果没有出现在 available_dimensions 中，只能作为 metric_mentions 的一部分，不能进入 dimension_mentions 或 dimension_slots。
- 普通维度槽位的 value 不得是时间表达；今天/昨天/本月/最近7天/近30天/去年同期/本财年/上财季/2026财年第2季度/按天/按月 等只能进入 time_mentions、time_range 或 query_shape.time_grain。
- 如果用户说“今天店铺销售额”，店铺是维度名但没有提供店铺值，必须输出 {"name":"店铺","role":"ambiguous","value":null,"value_status":"not_provided"}，并将“今天”放入 time_range。
- time_mentions：用户原文或重写问题里出现的时间范围、时间粒度或时间表达，例如“最近 7 天”“按月”“今天”。
- time_range：如果识别到时间范围，输出 {"raw":"今天","value_status":"provided"}；没有识别到时输出 {"raw":null,"value_status":"not_provided"}。
- filter_mentions：用户原文或重写问题里出现的筛选条件，格式为 {"name": "自然语言条件名", "value": "自然语言条件值"}；没有明确条件时返回空数组。
- required_slot_types：后续生成可执行查询所需的槽位类型，只能使用 metric、dimension、time_dimension、time_range、filter、order、limit、comparison_target。
- query_shape：只描述查询形态，不引用任何真实资产 ID。可包含 needs_group_by、needs_order_by、order_direction、limit、time_grain、select_mode 等字段。
- subject_domain：如果 user_payload.subject_domains 有多个候选主题域，必须识别问题属于哪个主题域；只能从候选主题域中选择，不得编造 domain_id。
  - 明确命中时输出 status=selected，并填写候选中的 domain_id、domain_name、domain_biz_name、confidence、reason、candidate_domain_ids。
  - 多个主题域都可能匹配时输出 status=ambiguous，candidate_domain_ids 填写可能的候选，并在 ambiguous_slots 中加入 subject_domain。
  - 没有任何候选主题域时输出 status=not_required。
- 你不能选择真实指标、维度或枚举值 ID；资产确认由后续知识检索节点完成。

{shared_metric_time_rules}

{shared_dimension_rules}

歧义和冲突判定：
- 如果不知道用户要分析的指标或业务对象，ambiguous_slots 包含 metric。
- 如果用户要求分组、排行或对比但没有给出维度，ambiguous_slots 包含 dimension。
- 如果用户使用“这个/那个/这些/上面”等指代且上下文无法解析，ambiguous_slots 包含 reference。
- 如果用户同时提出互相冲突的时间粒度或分析目标，conflict_slots 包含 time_grain 或 intent。
- confidence 必须是 0 到 1 之间的数字；低于 0.8 会触发意图澄清。
""".strip().replace(
        "{shared_metric_time_rules}",
        METRIC_TIME_EXTRACTION_RULES,
    ).replace(
        "{shared_dimension_rules}",
        DIMENSION_EXTRACTION_RULES,
    )
    user_payload = {
        "rewritten_question": rewritten_question,
        "conversation_context": conversation_context or {},
        "user_feedback": user_feedback or {},
        "subject_domains": domain_candidates,
        "available_dimensions": dimension_candidates,
    }
    user_prompt = "请识别以下 ChatBI 问题的分析意图，并严格返回 JSON：\n" + json.dumps(
        user_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def build_intent_shape_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造分析形态识别提示词。"""

    domain_candidates = normalize_subject_domain_candidates(subject_domains or [])
    system_prompt = """
# 角色

你是 ChatBI 工作流中的分析形态识别器，只负责识别分析类型、查询形态、必需槽位和主题域。

# 输出

只输出 JSON 对象。

```json
{
  "intent_type": "metric_query | trend_analysis | ranking_analysis | comparison_analysis | detail_query | share_analysis | anomaly_analysis | unknown",
  "confidence": 0.0,
  "required_slot_types": [],
  "query_shape": {},
  "subject_domain": {"status": "not_required | selected | ambiguous | not_matched", "domain_id": null, "domain_name": null, "domain_biz_name": null, "confidence": 0.0, "reason": "", "candidate_domain_ids": []},
  "ambiguous_slots": [],
  "conflict_slots": []
}
```

# 规则

- `metric_query`：查询指标值或统计值。
- `trend_analysis`：趋势、走势、按天、按周、按月变化。
- `ranking_analysis`：排行、最高、最低、TopN、前 N、后 N。
- `comparison_analysis`：同比、环比、对比、较上期、多个对象比较。
- `detail_query`：明细、详情、列表、清单。
- `share_analysis`：占比、构成、比例。
- `anomaly_analysis`：异常、波动、下降原因。
- `unknown`：无法判断分析类型。
- `required_slot_types` 只能使用 `metric`、`dimension`、`time_dimension`、`time_range`、`filter`、`order`、`limit`、`comparison_target`。
- `query_shape` 只描述查询形态，可包含 `select_mode`、`needs_group_by`、`needs_order_by`、`order_direction`、`limit`、`time_grain`。
""".strip()
    user_prompt = markdown_user_prompt(
        rewritten_question=rewritten_question,
        subject_domains=domain_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请识别分析类型、查询形态、必需槽位和主题域，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def build_semantic_mentions_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造指标和时间线索识别提示词。"""

    dimension_candidates = normalize_dimension_candidates(
        available_dimensions or []
    )
    system_prompt = """
# 角色

你是 ChatBI 工作流中的“指标线索和时间线索识别器”。

你只负责从用户问题中抽取：
1. 指标线索
2. 时间线索

你不负责识别分组、筛选、对比、排序、维度绑定、指标 ID、字段名、业务口径或 SQL 语义。

# 输出格式

只输出一个 JSON 对象，结构必须完全符合以下格式：

{
  "metric_mentions": [],
  "time_mentions": [],
  "time_range": {
    "raw": null,
    "value_status": "provided | not_provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

{shared_metric_time_rules}

# Graph 线索约束
`available_dimensions` 如果存在，只能作为辅助理解维度语义的参考，不是指标抽取的白名单或黑名单。
不要输出指标 ID、字段名、`biz_name` 或任何系统内部标识。

# 示例

用户问题：

最近7天销售额是多少？

输出：

{
  "metric_mentions": ["销售额"],
  "time_mentions": ["最近7天"],
  "time_range": {
    "raw": "最近7天",
    "value_status": "provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

用户问题：

按城市看本月订单数

输出：

{
  "metric_mentions": ["订单数"],
  "time_mentions": ["本月"],
  "time_range": {
    "raw": "本月",
    "value_status": "provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

用户问题：

北京 App 端的销售额

输出：

{
  "metric_mentions": ["销售额"],
  "time_mentions": [],
  "time_range": {
    "raw": null,
    "value_status": "not_provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

用户问题：

看一下北京最近7天的数据

输出：

{
  "metric_mentions": [],
  "time_mentions": ["最近7天"],
  "time_range": {
    "raw": "最近7天",
    "value_status": "provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

用户问题：

分析一下订单

输出：

{
  "metric_mentions": [],
  "time_mentions": [],
  "time_range": {
    "raw": null,
    "value_status": "not_provided"
  },
  "ambiguous_slots": ["订单"],
  "conflict_slots": []
}

用户问题：

最近7天的上月销售额

输出：

{
  "metric_mentions": ["销售额"],
  "time_mentions": ["最近7天", "上月"],
  "time_range": {
    "raw": "最近7天的上月",
    "value_status": "provided"
  },
  "ambiguous_slots": [],
  "conflict_slots": ["最近7天的上月"]
}
""".strip().replace(
        "{shared_metric_time_rules}",
        METRIC_TIME_EXTRACTION_RULES,
    )
    user_prompt = markdown_user_prompt(
        rewritten_question=rewritten_question,
        available_dimensions=dimension_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请抽取指标线索和时间线索，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
