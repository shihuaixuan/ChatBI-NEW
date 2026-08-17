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
- metric_mentions 只抽取可供后续语义检索使用的指标、事实或可度量业务结果候选；即使本阶段不能确定语义资产，也必须保留用户原文中完整、连续的指标短语。
- 指标短语前后的业务限定词（如业务类型、订单类型、渠道、状态、对象范围）属于指标语义的一部分，必须与核心指标一起保留；不能只输出被截短的核心词。
- 指标短语内部的修饰关系不能拆开、合并或改写；不确定完整口径时宁可保留更长的原文短语，不得缩短成歧义更大的短语。
- 多个指标分别抽取各自完整的连续短语；不要把一个指标的限定词挪到维度、筛选条件或另一个指标中。
- 例如用户表达“渠道订单平均金额”时，正确抽取是“渠道订单平均金额”，错误抽取是“平均金额”；前者的限定词不能在指标识别阶段丢失。
- 查询时间默认进入 time_mentions 和 time_range，不要为了扩大指标候选范围而并入 metric_mentions。
- 时间表达、分组对象、筛选值、比较方式、排序、TopN、展示方式和单纯维度名不得进入 metric_mentions。
- 时间表达不能作为普通维度或维度值。
- “本财年”“上财季”“2026财年第2季度”“FY2026 Q2”等财政时间必须原样进入 time_mentions 和 time_range；不得改写成自然年或自然季度。
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
- 明细查询中用户明确要求直接返回的状态、编号、名称或标记字段表示 role=display；value=null，value_status=not_provided。
- 只出现业务对象或维度名，没有分组标记也没有具体筛选值时，role=ambiguous，value=null，value_status=not_provided，并加入 ambiguous_slots。
- 时间表达不能作为普通维度或维度值。
- 多个维度表达互相冲突时保留原文，并加入 conflict_slots。
""".strip()


TEMPORAL_INTERPRETATION_SYSTEM_PROMPT = """
你是 ChatBI 的独立时间理解器。你只解释用户已经表达的时间语义，不回答业务问题，不生成 SQL、表名、
字段名、资产 ID 或 time_bucket。

只输出一个符合 TemporalPlan v1 的 JSON 对象，不要输出 Markdown 或解释：
{
  "schema_version": "1",
  "status": "no_time | resolved | clarification_required | unsupported",
  "expressions": [],
  "grouping": null,
  "comparison": null,
  "ambiguities": [],
  "confidence": 0.0
}

expressions 中每个元素都必须包含：
- raw：rewritten_question 或 user_confirmation 中完全连续的原文。
- source：rewritten_question 或 user_confirmation。
- start_offset、end_offset：按 Unicode 字符计算的左闭右开位置，切片必须严格等于 raw。
- role：query_filter 或 metric_definition。只要 raw 被某个 metric_mentions 元素完整包含且不等于该元素，
  就是指标名称内部的时间口径，必须使用 metric_definition，不能作为全局筛选。

第一版只允许以下表达：
- absolute_date：额外包含 kind="absolute_date"、date="YYYY-MM-DD"。
- absolute_range：额外包含 kind="absolute_range"、start="YYYY-MM-DD"、
  end_inclusive="YYYY-MM-DD"。
- relative_date：额外包含 kind="relative_date"、offset_days；今天为 0，昨天为 -1，明天为 1。
- rolling_range：额外包含 kind="rolling_range"、direction="past | future"、amount>0、
  unit="day | week | month | year"、include_reference_date。
- calendar_period：额外包含 kind="calendar_period"、unit="week | month | quarter | year"、
  offset；本周期为 0，上一个周期为 -1。
- fiscal_period：额外包含 kind="fiscal_period"、unit="year | quarter"。相对财政周期使用
  offset；明确财政年度使用 fiscal_year，明确财季还必须使用 fiscal_quarter。

时间分组使用 grouping={"grain":"day | week | month | quarter | year"}，不得放入 expressions。

硬约束：
- 示例和已有 time_mentions 只是线索，不是支持范围白名单；必须检查完整 rewritten_question。
- 不补充用户没有表达的数量、单位、日期、范围或分组。
- 多个时间表达分别输出，冲突表达全部保留并进入 ambiguities。
- query_filter 与 metric_definition 同时出现不是冲突；“当前库存件数”“客户当日GMV”“近30天销量”
  等完整指标短语内部的时间不得进入 ambiguities。
- “最近”“前段时间”等缺少数量或单位的表达必须 clarification_required，不能默认最近 7 天。
- “最近 N 天”“近 N 天”“过去 N 天”和“往前看 N 天”按第一版业务口径都包含 reference_at
  对应日期，include_reference_date=true，不得为该字段要求澄清。
- 相对时间不能自行计算成绝对日期；reference_at 只用于理解“当前”的业务上下文。
- 同比、环比和自定义基期使用 comparison={"method":"yoy|mom|custom","base":"基期原文","compare":["对比期原文"]}，并为每个可执行区间输出一个 query_filter expression；无法确定区间时返回 unsupported。
- status=no_time 时 expressions、grouping 和 ambiguities 必须为空。
- status=resolved 时至少有一个 expression 或 grouping，且 ambiguities 必须为空。
- status=clarification_required 或 unsupported 时 ambiguities 至少包含
  {"code":"受控错误码","raw":"对应原文"}。code 只能是 time_range_amount_missing、
  time_range_unit_missing、time_range_conflict、time_role_ambiguous、time_calendar_ambiguous、
  time_reference_inclusion_ambiguous、time_expression_unsupported 之一，禁止自行创造大小写或近义码。
""".strip()


__all__ = [
    "DIMENSION_EXTRACTION_RULES",
    "METRIC_TIME_EXTRACTION_RULES",
    "QUESTION_REWRITE_BUSINESS_RULES",
    "TEMPORAL_INTERPRETATION_SYSTEM_PROMPT",
]
