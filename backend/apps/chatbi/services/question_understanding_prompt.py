"""Agent 与 Graph 共享的问题理解提示词业务规则。"""

QUESTION_REWRITE_BUSINESS_RULES = """
语义保真规范化原则：
- 不回答问题，不生成 SQL，不选择或提及工具。
- 保持修饰关系、归属关系、并列关系和筛选关系，保留完整业务短语边界。
- 不拆分或重组指标短语内部的业务修饰关系。
- 不新增用户没有表达的分组、筛选、比较、排序或明细意图，也不新增指标、维度、时间和默认值。
- 对并列指标、并列对象或并列条件，只做语义清晰化，不合并、不丢弃、不改写成上下级关系。
- 只有当前输入真正依赖上下文时才继承上一轮；只替换用户本轮明确提到的槽位，其余槽位沿用可确认语义。
- 当前输入可以独立理解时必须保持当前输入，不得被历史问题覆盖。
- 当前输入明显在回答挂起澄清时，将回答合并到挂起问题对应的原问题中。
- 无法可靠衔接上下文或存在多个合理解释时，不要强行补全；要求用户澄清并列出缺失槽位。
""".strip()


METRIC_TIME_EXTRACTION_RULES = """
指标和时间抽取规则：
- 只抽取用户明确表达的线索，不推断、补全、改写或标准化业务口径。
- metric_mentions 只包含指标、事实或可度量业务结果，并保留“支付订单数”“新增用户数”等完整修饰关系。
- 时间表达、分组对象、筛选值、比较方式、排序、TopN、展示方式和单纯维度名不得进入 metric_mentions。
- 时间表达不能作为普通维度或维度值。
- 用户没有明确表达指标时，metric_mentions 必须为空，不得猜测指标。
- 多个时间或指标表达互相冲突时，保留原文并加入 conflict_slots。
""".strip()


DIMENSION_EXTRACTION_RULES = """
维度抽取规则：
- 独立检查问题中的每个显式业务对象、分析对象和限定对象，不能依赖其他子任务补充维度线索。
- 排除明确的指标短语、时间表达、排序、比较和展示方式后，剩余业务对象如果可能承担分组或筛选作用，必须进入 dimension_mentions 和 dimension_slots。
- dimension_mentions 与 dimension_slots 必须一致，每个 mention 都必须有且只有一个同名槽位。
- 不从指标短语内部强拆“新增、支付、成交、累计”等指标修饰词作为维度。
- “按/各/每个/分……统计”表示 role=group_by，value=null，value_status=not_provided。
- 维度带有明确值时表示 role=filter；value 必须保留用户表达的值并设置 value_status=provided。
- 只出现业务对象或维度名，没有分组标记也没有具体筛选值时，role=ambiguous，value=null，value_status=not_provided，并加入 ambiguous_slots。
- 时间表达不能作为普通维度或维度值。
- 多个维度表达互相冲突时保留原文，并加入 conflict_slots。
""".strip()


__all__ = [
    "DIMENSION_EXTRACTION_RULES",
    "METRIC_TIME_EXTRACTION_RULES",
    "QUESTION_REWRITE_BUSINESS_RULES",
]
