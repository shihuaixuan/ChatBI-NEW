"""ChatBI 问题理解应用服务。"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol, TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from apps.chatbi.errors import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
    QuestionUnderstandingError,
    TemporalInterpretationError,
)
from apps.chatbi.models.dto.mention import (
    MentionGraph,
    normalize_mention_graph_payload,
    project_mention_graph_to_intent,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelResponse,
    QuestionModelResult,
)
from apps.chatbi.models.dto.question_understanding import (
    DimensionSlot,
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionRewriteOutput,
    QuestionUnderstandingOutcome,
    QuestionUnderstandingOutput,
    QuestionUnderstandingValidationData,
    RequiredSlotType,
    TemporalInterpretationResult,
    TemporalShadowObservation,
    TimeRange,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    dimension_candidate_by_text,
    dimension_candidate_from_schema_element,
    dimension_text_key,
    normalize_dimension_candidates,
)
from apps.chatbi.services.understanding.model_invocation import StructuredModelService
from apps.chatbi.services.understanding.normalization import (
    apply_field_patches,
    assert_repair_invariants,
    build_patch_request,
    normalize_model_payload,
    parse_patch_payload,
)
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
    MENTION_GRAPH_SYSTEM_PROMPT,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.chatbi.services.understanding.temporal_interpretation import (
    TemporalInterpretationService,
    apply_temporal_interpretation_payload,
    compare_temporal_shadow,
)
from apps.chatbi.services.understanding.validation import (
    validate_question_understanding,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.temporal import (
    TemporalContext,
    build_run_temporal_context,
    normalize_time_range_payload,
)
from apps.trace import (
    AgentTraceRecorder,
    DisabledAgentTraceRecorder,
    TraceNodeHandle,
    TraceNodeSpec,
    TraceNodeStatus,
    TraceNodeType,
    llm_attributes,
)

QuestionUnderstandingModelResponse = QuestionModelResponse


class QuestionUnderstandingModelClient(Protocol):
    """问题理解模型协议，测试可注入确定性实现。"""

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionUnderstandingModelResponse: ...


REWRITE_SYSTEM_PROMPT = "\n\n".join(
    [
        """
你是智能问数场景下的问题重写大师。

你的唯一任务是：根据用户当前问题和必要的会话上下文，将当前问题重写为一个完整、明确、专业的在问数场景下的自然语言问题。

重写规则：
1. 保留用户当前问题中明确表达的指标、维度、筛选条件、时间范围和展示要求。
2. 消解代词、省略和上下文引用。
3. 只有在历史上下文中存在唯一明确指向时，才能补全省略内容。
4. 当前问题是对上一轮问题的修改时，只修改用户明确要求修改的部分，保留其他未修改条件。
5. 当前问题可以独立表达完整含义时，不继承上一轮无关内容。
6. 保留会影响后续模式分类的查询动作和分析关系，包括同查多个指标、比较不同时间或对象、差值、占比、增长率、趋势、排名、下钻和原因分析。
7. 可以把口语化表达改写为明确的自然语言，但不能改变用户原意。
8. 可以补全明确的相对时间表达，但不能猜测无法确定的时间。
9. 保留用户原有的业务名称、指标名称、店铺编号、区域名称、渠道名称和筛选值。
10. 不得增加用户没有提出的指标、维度、筛选条件、排序、Top N、比较关系、计算关系或分析目标。
11. 不得将业务名称转换为表名、字段名、内部资产 ID 或 SQL。
12. 不得输出问题分类、意图、模式、置信度、缺失字段、上下文继承字段或解释说明。
13. 如果上下文存在多个可能指向，无法唯一确定时，不得自行猜测；在 rewrite_question 中保留无法确定的原有表达，交由后续流程处理。

只输出一个合法 JSON 对象，且只能包含以下两个字段：
{
  "original_question": "用户本轮提交的原始问题",
  "rewrite_question": "重写后的完整问题"
}
字段要求：
- original_question 必须完整保留用户本轮提交的问题原文；
- rewrite_question 必须是供后续流程使用的完整自然语言问题；
- rewrite_question 不得包含 JSON、Markdown、解释过程或 SQL；
- 不得输出 JSON 以外的文字。
""".strip(),
        QUESTION_REWRITE_BUSINESS_RULES,
        """
典型示例：

示例 1：可独立理解的新问题
输入：{"current_question":"本月新增客户数是多少？","conversation_context":{}}
输出：{"original_question":"本月新增客户数是多少？","rewrite_question":"本月新增客户数是多少？"}

示例 2：只替换上一轮时间的追问
输入：{"current_question":"那上个月呢？","conversation_context":{"last_rewritten_question":"查询本月新增客户数"}}
输出：{"original_question":"那上个月呢？","rewrite_question":"查询上个月新增客户数"}

示例 3：无法确定引用对象
输入：{"current_question":"那另一个呢？","conversation_context":{}}
输出：{"original_question":"那另一个呢？","rewrite_question":"那另一个呢？"}
""".strip(),
    ]
)


INTENT_SYSTEM_PROMPT = "\n\n".join(
    [
        """
你是 ChatBI 的分析形态、指标和时间意图识别器。不回答问题，不生成 SQL，不选择工具，也不绑定资产 ID、字段名或表名。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "intent_type": "metric_query | trend_analysis | ranking_analysis | comparison_analysis | detail_query | share_analysis | composition | multi_step | anomaly_analysis | unknown",
  "confidence": 0.0,
  "metric_mentions": [],
  "time_mentions": [],
  "time_range": {"raw": null, "value_status": "provided | not_provided"},
  "time_ranges": [],
  "comparison": null,
  "composition": null,
  "multi_step": null,
  "query_shape": {
    "select_mode": "aggregate | detail",
    "needs_group_by": false,
    "needs_order_by": false,
    "order_direction": "asc | desc | null",
    "limit": null,
    "time_grain": "day | week | month | quarter | year | null",
    "comparison_type": "yoy | mom | custom | null"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}

分析形态：
- metric_query：指标值或统计值。
- trend_analysis：趋势、走势或按时间粒度变化。
- ranking_analysis：排行、最高、最低、TopN。
- comparison_analysis：同比、环比、较上期或多个对象比较；比较时补充 comparison={"base":"...","compare":["..."],"method":"yoy|mom|custom"}。
- composition：占比、构成和贡献度；multi_step：下钻或归因的多步分析。
- detail_query：明细、列表、清单。
- share_analysis：占比、构成、比例。
- anomaly_analysis：异常、波动或变化原因。
""".strip(),
        METRIC_TIME_EXTRACTION_RULES,
        """
Agent 输出约束：
- 不输出 dimension_mentions、dimension_slots、filter_mentions 和 required_slot_types；维度与筛选由并行的独立维度任务识别，必需槽位由服务端派生。
- metric_mentions 只提供后续语义检索所需的指标候选；不要求本阶段确定完整指标口径，但必须保留用户原文中完整、连续的指标短语及其业务限定词，不得缩短。
- query_shape 必须完整输出全部字段，只表示用户问题中的查询组织语义，不得绑定资产或生成 SQL。
- detail_query 的 select_mode=detail；ranking_analysis 如果是对明细行排序也可以是 detail，其他情况为 aggregate。
- 只有用户表达分组、趋势分桶、排名、比较或占比时，needs_group_by 才能为 true。
- 只有用户表达排序或排名时，needs_order_by 才能为 true；否则 order_direction 必须为 null。
- “最高、最多、最大、前N、TopN”对应 order_direction=desc；“最低、最少、最小、后N”对应 asc。
- limit 只填写用户明确表达的 1～1000 整数，没有明确数量时必须为 null，不得补默认 TopN。
- time_grain 只填写用户明确表达的按天、周、月、季度或年粒度，没有明确粒度时必须为 null。

典型示例：

示例 1：排名查询
输入：{"rewritten_question":"本月新增客户数最高的5个店铺是哪些？","inherited_context":{}}
输出：{"intent_type":"ranking_analysis","confidence":0.98,"metric_mentions":["新增客户数"],"time_mentions":["本月"],"time_range":{"raw":"本月","value_status":"provided"},"query_shape":{"select_mode":"aggregate","needs_group_by":true,"needs_order_by":true,"order_direction":"desc","limit":5,"time_grain":null},"ambiguous_slots":[],"conflict_slots":[]}

示例 2：按天趋势
输入：{"rewritten_question":"查看最近7天每天的销售额趋势","inherited_context":{}}
输出：{"intent_type":"trend_analysis","confidence":0.98,"metric_mentions":["销售额"],"time_mentions":["最近7天"],"time_range":{"raw":"最近7天","value_status":"provided"},"query_shape":{"select_mode":"aggregate","needs_group_by":true,"needs_order_by":false,"order_direction":null,"limit":null,"time_grain":"day"},"ambiguous_slots":[],"conflict_slots":[]}

示例 3：普通指标查询
输入：{"rewritten_question":"今天店铺100011的活跃客户数是多少？","inherited_context":{}}
输出：{"intent_type":"metric_query","confidence":0.98,"metric_mentions":["活跃客户数"],"time_mentions":["今天"],"time_range":{"raw":"今天","value_status":"provided"},"query_shape":{"select_mode":"aggregate","needs_group_by":false,"needs_order_by":false,"order_direction":null,"limit":null,"time_grain":null},"ambiguous_slots":[],"conflict_slots":[]}
""".strip(),
    ]
)


DIMENSION_SYSTEM_PROMPT = "\n\n".join(
    [
        """
你是 ChatBI 的自然语言维度槽位识别器。你只识别维度、维度用法和明确筛选条件，不回答问题，不生成 SQL，不选择工具，也不绑定资产 ID、字段名或表名。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "自然语言维度名", "role": "group_by | filter | display | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
  ],
  "residual_filter_mentions": [
    {"name": "无法归属到可用维度的条件名", "value": "条件值", "operator": "="}
  ],
  "ambiguous_slots": [],
  "conflict_slots": []
}
""".strip(),
        DIMENSION_EXTRACTION_RULES,
        """
Agent 示例：
- “时间 + 业务对象 + 的 + 指标”中的业务对象不能被省略；没有分组标记或具体值时仍然是 ambiguous。

- 命中 available_dimensions 的名称或 aliases 时，dimension_slots[].name 必须使用候选中的标准 name。
- dimension_slots[].value 只填写值本身，不包含维度名、别名、“为”或“=”等连接文本。
- 同一维度包含多个明确筛选值时，value 必须输出数组，例如 value=["100011", "100012"]；禁止拼接成逗号字符串。
- 已进入 dimension_slots 的筛选条件禁止重复写入 residual_filter_mentions。
- residual_filter_mentions 只保留无法归属到任何 available_dimensions 的剩余条件，每个元素必须是对象，不能输出字符串。
- “最高的5个门店”“最低的3个商品”中的门店、商品是被排名对象，role=group_by。
- “各渠道占比”中的渠道是构成维度，role=group_by。
- 明细查询中“订单金额、状态和是否超时”等直接返回的字段，role=display。
- “比较北京和上海的销售额”中承载北京、上海的维度是比较维度；有明确值时 role=filter 且 value 为值数组。
- “今天门店的客户数”：门店 role=ambiguous。
- “今天各门店的客户数”：门店 role=group_by。
- “今天1号门店的客户数”：门店 role=filter，value="1号"。
- “比较店铺100011和100012的GMV”：店铺 role=filter，value=["100011", "100012"]。
- “今天新增客户数”：新增属于指标修饰词，不输出维度。

典型示例：

示例 1：排名对象
输入：{"rewritten_question":"本月新增客户数最高的5个店铺是哪些？","available_dimensions":[{"name":"店铺ID","aliases":["店铺"]}],"time_dimensions":[{"name":"时间","aliases":[]}]}
输出：{"dimension_mentions":["店铺ID"],"dimension_slots":[{"name":"店铺ID","role":"group_by","value":null,"value_status":"not_provided","value_confidence":1.0}],"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}

示例 2：明确筛选值
输入：{"rewritten_question":"今天店铺100011的活跃客户数是多少？","available_dimensions":[{"name":"店铺ID","aliases":["店铺"]}],"time_dimensions":[{"name":"时间","aliases":[]}]}
输出：{"dimension_mentions":["店铺ID"],"dimension_slots":[{"name":"店铺ID","role":"filter","value":"100011","value_status":"provided","value_confidence":1.0}],"residual_filter_mentions":[],"ambiguous_slots":[],"conflict_slots":[]}

示例 3：用途不明确
输入：{"rewritten_question":"最近7天店铺的新增客户数是多少？","available_dimensions":[{"name":"店铺ID","aliases":["店铺"]}],"time_dimensions":[{"name":"时间","aliases":[]}]}
输出：{"dimension_mentions":["店铺ID"],"dimension_slots":[{"name":"店铺ID","role":"ambiguous","value":null,"value_status":"not_provided","value_confidence":0.5}],"residual_filter_mentions":[],"ambiguous_slots":["店铺ID"],"conflict_slots":[]}
""".strip(),
    ]
)


QUESTION_UNDERSTANDING_SYSTEM_PROMPT = "\n\n".join(
    [
        """
你是 ChatBI 的统一问题理解器。你只把问题转换为完整的分析语义结构，不回答问题，不生成 SQL，不选择工具。

问题重写完成后，本任务一次性识别：指标、时间、维度及其用途、筛选条件、查询形态和排名结构。
指标、排名对象和排名数量必须放在同一个语义结果中判断，不能把维度识别成与意图无关的独立任务。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "category": "chitchat | data_query | meta_query | out_of_scope",
  "intent_type": "metric_query | trend_analysis | ranking_analysis | comparison_analysis | detail_query | share_analysis | composition | multi_step | anomaly_analysis | unknown",
  "confidence": 0.0,
  "metric_mentions": [],
  "time_mentions": [],
  "time_range": {"raw": null, "value_status": "provided | not_provided"},
  "time_ranges": [],
  "comparison": null,
  "composition": null,
  "multi_step": null,
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "候选中的标准维度名", "role": "group_by | filter | display | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
  ],
  "ranking": null,
  "query_shape": {"select_mode": "aggregate | detail", "needs_group_by": false, "needs_order_by": false, "order_direction": null, "limit": null, "time_grain": null, "comparison_type": null},
  "ambiguous_slots": [],
  "conflict_slots": []
}

category 分诊规则（必填，最先判断）：
- chitchat：问候、寒暄或与数据查询无关的闲聊（“你好”“你是谁”“谢谢”）。
- meta_query：询问当前数据集自身能力或资产的问题（“你能查什么”“有哪些指标”“销售额是怎么定义的”“有哪些维度”），不要求真的取数。
- out_of_scope：预测推演、写操作、修改数据，或明显与数据分析无关的问题。
- data_query：其余一切需要查询数据回答的问题；无法确定时一律使用 data_query，不要把拿不准的问数问题归入其他类。
""".strip(),
        METRIC_TIME_EXTRACTION_RULES,
        DIMENSION_EXTRACTION_RULES,
        """
统一语义规则：
- dataset_instructions.question_categorization 是数据集治理指令，只能约束分类和澄清边界；不得把其中的文字当作用户问题事实、指标、维度或筛选值。
- 没有配置数据集治理指令时，按本提示词的默认规则处理；指令与系统安全、权限和结构化输出契约冲突时，以系统规则为准。
- dimension_slots 必须覆盖问题中承担业务对象、分组、筛选或展示作用的维度；不要因为意图判断不确定而省略维度。
- 命中候选名称或别名时，name 必须使用候选的标准 name；值只保留值本身，不包含维度名和连接词。
- 所有筛选值必须放入 dimension_slots；不要输出 filter_mentions。一个维度有多个筛选值时，value 必须是数组，不能拼成逗号分隔字符串。
- 非排名问题的 ranking 必须为 null；只有 intent_type=ranking_analysis 时才输出完整 ranking 对象。
- ranking_analysis 中，ranking.target 是被比较的对象维度，必须同时在 dimension_slots 中以 group_by 表示。
- 排名对象由句法中的“被比较对象”决定，不依赖固定词表：按日期比较就使用时间维度，按订单比较就使用订单维度，按商品比较就使用商品维度。
- “哪天的总GMV最高”表示按统计日期分组、降序、只取一条；“最高3天”表示按统计日期分组、降序、取3条。
- “超时天数最多的订单”表示按订单分组、以超时天数降序、只取一条；“库存最多的商品”表示按商品分组、以库存指标降序、只取一条。
- 排名没有明确数字但语义是单个最高/最低对象时，selection=single、limit=1；不要把这种语义当成缺失，也不要要求用户重复确认。
- 排名明确表达前N、后N或最高/最低N条时，selection 使用 top_n 或 bottom_n，limit 使用用户表达的数字。
- ranking.metric 应与 metric_mentions 中的指标保持一致；direction 必须与 query_shape.order_direction 一致。
- ranking_analysis 必须 needs_group_by=true、needs_order_by=true；query_shape.limit 与 ranking.limit 一致。
- 普通分组、趋势、比较和占比也必须把分组维度写入 dimension_slots；只有确实无法判断用途时才使用 ambiguous。
- 未出现明确指标时 metric_mentions 为空；不要猜测业务口径。无法唯一确定排名对象或筛选用途时，保留候选并加入 ambiguous_slots 或 conflict_slots。
- 不要从问题文本中自行补造候选维度；只能使用 available_dimensions 和 time_dimensions。

典型示例：

示例 1：自然表达的日期排名
输入：{"rewritten_question":"2026年6月8日至14日，店铺100021哪一天的总GMV最高？","available_dimensions":[{"name":"档口ID","aliases":["店铺"]}],"time_dimensions":[{"name":"统计日期","aliases":[]}]}
输出：{"category":"data_query","intent_type":"ranking_analysis","confidence":0.99,"metric_mentions":["总GMV"],"time_mentions":["2026年6月8日至14日"],"time_range":{"raw":"2026年6月8日至14日","value_status":"provided"},"dimension_mentions":["档口ID","统计日期"],"dimension_slots":[{"name":"档口ID","role":"filter","value":"100021","value_status":"provided","value_confidence":1.0},{"name":"统计日期","role":"group_by","value":null,"value_status":"not_provided","value_confidence":1.0}],"ranking":{"target":"统计日期","metric":"总GMV","direction":"desc","selection":"single","limit":1},"query_shape":{"select_mode":"aggregate","needs_group_by":true,"needs_order_by":true,"order_direction":"desc","limit":1,"time_grain":"day"},"ambiguous_slots":[],"conflict_slots":[]}

示例 2：排名对象和数量在名词短语中
输入：{"rewritten_question":"库存最多的商品","available_dimensions":[{"name":"商品ID","aliases":["商品"]}],"time_dimensions":[]}
输出：{"category":"data_query","intent_type":"ranking_analysis","confidence":0.98,"metric_mentions":["库存"],"time_mentions":[],"time_range":{"raw":null,"value_status":"not_provided"},"dimension_mentions":["商品ID"],"dimension_slots":[{"name":"商品ID","role":"group_by","value":null,"value_status":"not_provided","value_confidence":1.0}],"ranking":{"target":"商品ID","metric":"库存","direction":"desc","selection":"single","limit":1},"query_shape":{"select_mode":"aggregate","needs_group_by":true,"needs_order_by":true,"order_direction":"desc","limit":1,"time_grain":null},"ambiguous_slots":[],"conflict_slots":[]}

示例 3：能力询问（元问题）
输入：{"rewritten_question":"你都能查哪些指标？","available_dimensions":[],"time_dimensions":[]}
输出：{"category":"meta_query","intent_type":"unknown","confidence":0.9,"metric_mentions":[],"time_mentions":[],"time_range":{"raw":null,"value_status":"not_provided"},"dimension_mentions":[],"dimension_slots":[],"ranking":null,"query_shape":{"select_mode":"aggregate","needs_group_by":false,"needs_order_by":false,"order_direction":null,"limit":null,"time_grain":null},"ambiguous_slots":[],"conflict_slots":[]}
""".strip(),
    ]
)


class QuestionUnderstandingService:
    """严格执行重写、统一问题理解和确定性校验，不提供静默降级。"""

    def __init__(
        self,
        model_client: QuestionUnderstandingModelClient | None = None,
        question_model_service: StructuredModelService | None = None,
        temporal_question_model_service: StructuredModelService | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
        temporal_interpretation_service: TemporalInterpretationService | None = None,
        temporal_shadow_enabled: bool = False,
        temporal_authority_enabled: bool = False,
        semantic_repair_v2_enabled: bool = False,
        mention_contract_enabled: bool = False,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> None:
        if mention_contract_enabled and not semantic_repair_v2_enabled:
            raise ValueError("MENTION_CONTRACT_REQUIRES_SEMANTIC_REPAIR_V2")
        effective_temporal_authority = (
            temporal_authority_enabled or semantic_repair_v2_enabled
        )
        if temporal_shadow_enabled and effective_temporal_authority:
            raise ValueError("TEMPORAL_INTERPRETATION_MODE_CONFLICT")
        if model_client is not None and question_model_service is not None:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SOURCE_CONFLICT")
        if model_client is not None:
            self._question_model_service = StructuredModelService(model_client)
        elif question_model_service is not None:
            self._question_model_service = question_model_service
        else:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED")
        self._schema_provider = schema_provider
        self._trace_recorder = trace_recorder or DisabledAgentTraceRecorder()
        self._temporal_shadow_enabled = temporal_shadow_enabled
        temporal_enabled = temporal_shadow_enabled or effective_temporal_authority
        if temporal_interpretation_service is not None and not temporal_enabled:
            raise ValueError("TEMPORAL_INTERPRETATION_SERVICE_DISABLED")
        # R0 开启后，时间模型必须成为唯一事实源，避免模型字段被剥离后语义丢失。
        self._temporal_authority_enabled = effective_temporal_authority
        self._semantic_repair_v2_enabled = semantic_repair_v2_enabled
        self._mention_contract_enabled = mention_contract_enabled
        self._temporal_interpretation_service = (
            temporal_interpretation_service
            or TemporalInterpretationService(
                temporal_question_model_service or self._question_model_service
            )
            if temporal_enabled
            else None
        )

    def understand(
        self,
        *,
        question: str,
        datasource_id: int | None,
        conversation_context: dict[str, Any] | None = None,
        tenant_id: int | None = None,
        dataset_id: int | None = None,
        temporal_context: TemporalContext | None = None,
    ) -> QuestionUnderstandingOutcome:
        trace_run_id = self._trace_recorder.current_run_id()
        fixed_temporal_context = temporal_context or build_run_temporal_context()
        context = conversation_context or {}
        with self._trace_node(
            trace_run_id,
            TraceNodeType.PHASE,
            "load_dimension_candidates",
            "加载维度候选",
            input_data={"tenant_id": tenant_id, "dataset_id": dataset_id},
        ) as candidate_node:
            available_dimensions, dataset_instructions = self._load_dataset_context(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
            if candidate_node is not None:
                candidate_node.set_output(
                    {
                        "candidate_count": len(available_dimensions),
                        "time_dimension_count": sum(
                            1 for item in available_dimensions if item.get("is_time")
                        ),
                    }
                )
                candidate_node.set_output_detail(
                    {"available_dimensions": available_dimensions}
                )
        rewrite_context = _build_rewrite_context(context)
        rewrite, rewrite_usage = self._invoke_validated_model(
            "QUESTION_REWRITE",
            REWRITE_SYSTEM_PROMPT,
            {
                "current_question": question,
                "conversation_context": rewrite_context,
                "reference_datetime": fixed_temporal_context.reference_at.isoformat(),
                "timezone": fixed_temporal_context.timezone,
            },
            QuestionRewriteOutput,
            # 迁移期间只接受旧字段 rewritten_question 的显式映射；旧的意图、
            # 置信度和槽位字段不会进入新的重写 DTO，也不会被后续流程消费。
            normalizer=lambda payload: _normalize_question_rewrite_payload(
                payload,
                original_question=question,
            ),
            trace_run_id=trace_run_id,
        )

        if rewrite.original_question != question:
            raise QuestionUnderstandingError(
                "QUESTION_REWRITE_ORIGINAL_QUESTION_MISMATCH"
            )

        understanding_payload = {
            "rewritten_question": rewrite.rewrite_question,
            # 重写阶段不再输出上下文继承对象；后续阶段只使用规范化后的问题文本。
            "inherited_context": {},
            # 数据集治理指令单独放在固定模块槽位，避免与用户语义事实混淆。
            "dataset_instructions": {
                "question_categorization": list(
                    dataset_instructions.get("question_categorization", [])
                )
            },
        }
        if not self._mention_contract_enabled:
            # R0 兼容路径仍使用旧的 schema 辅助理解；R1 明确禁止资产清单进入提及抽取。
            understanding_payload.update(
                {
                    "available_dimensions": [
                        item for item in available_dimensions if not item.get("is_time")
                    ],
                    "time_dimensions": [
                        item for item in available_dimensions if item.get("is_time")
                    ],
                }
            )
        # 意图、排名对象和维度用途必须由同一次模型调用共同判断，避免并行结果互相缺少上下文。
        with self._trace_node(
            trace_run_id,
            TraceNodeType.PHASE,
            "question_understanding",
            "统一问题理解",
            input_data={"rewrite_question": rewrite.rewrite_question},
        ) as understanding_node:
            understanding_system_prompt = (
                MENTION_GRAPH_SYSTEM_PROMPT
                if self._mention_contract_enabled
                else QUESTION_UNDERSTANDING_SYSTEM_PROMPT
            )
            if self._semantic_repair_v2_enabled and not self._mention_contract_enabled:
                understanding_system_prompt += (
                    "\n\nR0 语义契约约束：时间与时段比较由独立 Temporal 任务唯一解释。"
                    "本次输出只登记 time_mentions，不得输出 time_range、time_ranges、"
                    "comparison 或 query_shape.comparison_type；不得根据数据集字段选择时间维度。"
                    "如果用户表达了变化、差值或增长率，只保留用户的指标和分析形态，"
                    "不要自行填写比较方法。"
                )
            mention_graph: MentionGraph | None = None
            if self._mention_contract_enabled:
                mention_graph, intent_usage = self._invoke_validated_model(
                    "QUESTION_UNDERSTANDING",
                    understanding_system_prompt,
                    understanding_payload,
                    MentionGraph,
                    normalizer=lambda payload: normalize_mention_graph_payload(
                        payload,
                        rewritten_question=rewrite.rewrite_question,
                    ),
                    trace_run_id=trace_run_id,
                )
                intent = project_mention_graph_to_intent(
                    mention_graph,
                    rewritten_question=rewrite.rewrite_question,
                )
            else:
                intent, intent_usage = self._invoke_validated_model(
                    "QUESTION_UNDERSTANDING",
                    understanding_system_prompt,
                    understanding_payload,
                    IntentRecognitionOutput,
                    normalizer=lambda payload: _normalize_unified_payload(
                        payload,
                        available_dimensions,
                        rewritten_question=rewrite.rewrite_question,
                    ),
                    trace_run_id=trace_run_id,
                )
            if understanding_node is not None:
                understanding_node.set_output(
                    {
                        "intent_type": intent.intent_type,
                        "metric_count": len(intent.metric_mentions),
                        "dimension_slot_count": len(intent.dimension_slots),
                        "ranking_target": (
                            intent.ranking.target if intent.ranking is not None else None
                        ),
                    }
                )
                understanding_node.set_output_detail(
                    {
                        "understanding": intent.model_dump(mode="json"),
                        "mention_graph": (
                            mention_graph.model_dump(mode="json")
                            if mention_graph is not None
                            else None
                        ),
                    }
                )
        intent = _stabilize_intent(
            _reconcile_detail_display_dimensions(
                intent,
                [] if self._mention_contract_enabled else available_dimensions,
                rewritten_question=rewrite.rewrite_question,
            ),
            fixed_temporal_context,
            # Agent 前置阶段只识别原始时间表达，实际解析交给 ReAct 的时间工具。
            # 旁路评估需要保留一份旧解析基线，但它不参与默认 Agent 执行。
            use_legacy_time_interpretation=self._temporal_shadow_enabled,
        )
        temporal_interpretation = None
        temporal_shadow = None
        with self._trace_node(
            trace_run_id,
            TraceNodeType.PHASE,
            "temporal_processing",
            "时间处理",
            input_data={
                "authority_enabled": self._temporal_authority_enabled,
                "time_mentions": intent.time_mentions,
            },
        ) as temporal_node:
            if self._temporal_authority_enabled:
                temporal_interpretation, temporal_usage = (
                    self._interpret_authoritative_plan(
                        rewritten_question=rewrite.rewrite_question,
                        intent=intent,
                        mention_graph=mention_graph,
                        temporal_context=fixed_temporal_context,
                        conversation_context=context,
                    )
                )
                intent = _apply_temporal_interpretation(
                    intent,
                    temporal_interpretation,
                    temporal_context=fixed_temporal_context,
                )
            else:
                temporal_shadow, temporal_usage = self._observe_temporal_plan(
                    rewritten_question=rewrite.rewrite_question,
                    intent=intent,
                    mention_graph=mention_graph,
                    temporal_context=fixed_temporal_context,
                    conversation_context=context,
                )
            if temporal_node is not None:
                if temporal_usage:
                    # 时间模型没有独立子节点时，由时间处理节点承载本次 Token。
                    temporal_node.set_token_usage(temporal_usage)
                temporal_node.set_output(
                    {
                        "mode": (
                            "authority" if self._temporal_authority_enabled else "legacy"
                        ),
                        "time_value_status": intent.time_range.value_status,
                        "shadow_status": (
                            temporal_shadow.status if temporal_shadow is not None else None
                        ),
                    }
                )
        with self._trace_node(
            trace_run_id,
            TraceNodeType.VALIDATION,
            "validate_question_understanding",
            "问题理解确定性校验",
            input_data={
                "intent_type": intent.intent_type,
                "ambiguous_slots": intent.ambiguous_slots,
                "conflict_slots": intent.conflict_slots,
            },
        ) as validation_node:
            validation = _validate_understanding(
                intent,
                temporal_interpretation=temporal_interpretation,
                pending_binding_enabled=self._semantic_repair_v2_enabled,
            )
            if validation_node is not None:
                validation_node.set_output(
                    {
                        "status": validation.status,
                        "reason_codes": validation.reason_codes,
                        "clarification_slots": validation.clarification_slots,
                    }
                )
        output = QuestionUnderstandingOutput(
            original_question=question,
            message_type=_derive_legacy_message_type(
                question,
                rewrite.rewrite_question,
                context,
            ),
            rewritten_question=rewrite.rewrite_question,
            inherited_context={},
            intent=intent,
            validation=validation,
            temporal_interpretation=temporal_interpretation,
            category=intent.category,
            mention_graph=mention_graph,
        )
        return QuestionUnderstandingOutcome(
            output=output,
            usage_metadata=_merge_usage(
                rewrite_usage,
                intent_usage,
                temporal_usage,
            ),
            temporal_shadow=temporal_shadow,
        )

    def _interpret_authoritative_plan(
        self,
        *,
        rewritten_question: str,
        intent: IntentRecognitionOutput,
        mention_graph: MentionGraph | None = None,
        temporal_context: TemporalContext,
        conversation_context: dict[str, Any],
        user_confirmation: str | None = None,
    ) -> tuple[TemporalInterpretationResult, dict[str, int]]:
        """调用共享时间任务；权威模式下任何模型或解析错误都向上抛出。"""

        service = self._temporal_interpretation_service
        if service is None or not self._temporal_authority_enabled:
            raise QuestionUnderstandingError("TEMPORAL_AUTHORITY_SERVICE_REQUIRED")
        raw_feedback = conversation_context.get("user_feedback")
        user_feedback = raw_feedback if isinstance(raw_feedback, dict) else {}
        return service.interpret_for_execution(
            rewritten_question=rewritten_question,
            metric_mentions=intent.metric_mentions,
            time_mentions=intent.time_mentions,
            temporal_context=temporal_context,
            conversation_context=conversation_context,
            analysis_context=_temporal_analysis_context(intent, mention_graph),
            user_feedback=user_feedback,
            user_confirmation=user_confirmation,
        )

    def resolve_temporal_clarification(
        self,
        *,
        understanding: dict[str, Any],
        answer: dict[str, Any],
        temporal_context: TemporalContext,
    ) -> QuestionUnderstandingOutcome:
        """只重新执行时间任务，并将用户确认结果写回既有问题理解。"""

        try:
            previous = QuestionUnderstandingOutput.model_validate(understanding)
        except ValidationError as exc:
            raise QuestionUnderstandingError(
                f"CLARIFICATION_CHECKPOINT_INVALID: {exc}"
            ) from exc
        confirmation, confirmed_plan = _temporal_clarification_answer(answer)
        if confirmed_plan is not None:
            service = self._temporal_interpretation_service
            if service is None or not self._temporal_authority_enabled:
                raise QuestionUnderstandingError("TEMPORAL_AUTHORITY_SERVICE_REQUIRED")
            temporal_interpretation = service.resolve_confirmed_plan(
                plan=confirmed_plan,
                rewritten_question=previous.rewritten_question,
                metric_mentions=previous.intent.metric_mentions,
                temporal_context=temporal_context,
                user_confirmation=confirmation,
                allowed_ambiguity_codes={
                    ambiguity.code
                    for ambiguity in (
                        previous.temporal_interpretation.plan.ambiguities
                        if previous.temporal_interpretation is not None
                        else ()
                    )
                },
            )
            usage_metadata: dict[str, int] = {}
        else:
            temporal_interpretation, usage_metadata = (
                self._interpret_authoritative_plan(
                    rewritten_question=previous.rewritten_question,
                    intent=previous.intent,
                    mention_graph=previous.mention_graph,
                    temporal_context=temporal_context,
                    conversation_context=previous.inherited_context,
                    user_confirmation=confirmation,
                )
            )
        intent = _apply_temporal_interpretation(
            previous.intent,
            temporal_interpretation,
            temporal_context=temporal_context,
        )
        output = previous.model_copy(
            update={
                "intent": intent,
                "validation": _validate_understanding(
                    intent,
                    temporal_interpretation=temporal_interpretation,
                ),
                "temporal_interpretation": temporal_interpretation,
            }
        )
        return QuestionUnderstandingOutcome(
            output=output,
            usage_metadata=usage_metadata,
        )

    def _observe_temporal_plan(
        self,
        *,
        rewritten_question: str,
        intent: IntentRecognitionOutput,
        mention_graph: MentionGraph | None = None,
        temporal_context: TemporalContext,
        conversation_context: dict[str, Any],
    ) -> tuple[TemporalShadowObservation | None, dict[str, int]]:
        """执行旁路时间理解；失败只进入观察结果，不替换当前权威时间范围。"""

        service = self._temporal_interpretation_service
        if service is None:
            return None, {}
        raw_feedback = conversation_context.get("user_feedback")
        user_feedback = raw_feedback if isinstance(raw_feedback, dict) else {}
        raw_confirmation = conversation_context.get("user_confirmation")
        user_confirmation = (
            raw_confirmation if isinstance(raw_confirmation, str) else None
        )
        try:
            outcome = service.interpret(
                rewritten_question=rewritten_question,
                metric_mentions=intent.metric_mentions,
                time_mentions=intent.time_mentions,
                temporal_context=temporal_context,
                conversation_context=conversation_context,
                analysis_context=_temporal_analysis_context(intent, mention_graph),
                user_feedback=user_feedback,
                user_confirmation=user_confirmation,
            )
        except TemporalInterpretationError as exc:
            return (
                TemporalShadowObservation(
                    status="model_error",
                    legacy_time_range=intent.time_range,
                    error_code=exc.code,
                ),
                exc.usage_metadata,
            )
        return (
            compare_temporal_shadow(
                plan=outcome.plan,
                legacy_time_range=intent.time_range,
                temporal_context=temporal_context,
            ),
            outcome.usage_metadata,
        )

    def _load_dimension_candidates(
        self,
        *,
        tenant_id: int | None,
        dataset_id: int | None,
    ) -> list[dict[str, Any]]:
        """读取当前语义数据集维度；已绑定数据集时加载失败必须明确终止。"""

        return self._load_dataset_context(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )[0]

    def _load_dataset_context(
        self,
        *,
        tenant_id: int | None,
        dataset_id: int | None,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        """一次加载维度候选和模块化治理指令，避免理解阶段重复读 Schema。"""

        if tenant_id is None or tenant_id <= 0 or dataset_id is None or dataset_id <= 0:
            return [], {}
        if self._schema_provider is None:
            raise QuestionUnderstandingError(
                "QUESTION_UNDERSTANDING_SCHEMA_PROVIDER_REQUIRED"
            )
        try:
            schema = self._schema_provider.build_dataset_schema(tenant_id, dataset_id)
        except Exception as exc:
            raise QuestionUnderstandingError(
                "QUESTION_UNDERSTANDING_SCHEMA_LOAD_FAILED"
            ) from exc
        candidates = normalize_dimension_candidates(
            [
                candidate
                for dimension in schema.dimensions
                if (candidate := dimension_candidate_from_schema_element(dimension))
                is not None
            ]
        )
        schema_instructions = getattr(schema, "instructions", {})
        instructions = {
            str(module): [str(content) for content in contents if str(content).strip()]
            for module, contents in (schema_instructions or {}).items()
            if isinstance(contents, list)
        }
        return candidates, instructions

    def _invoke_validated_model(
        self,
        stage: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        model_type: type[ModelType],
        *,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        validation_fallback: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        trace_run_id: int | None = None,
    ) -> tuple[ModelType, dict[str, int]]:
        """调用结构化模型并按开关选择旧重试或 R0 字段补丁协议。"""

        if self._semantic_repair_v2_enabled:
            return self._invoke_validated_model_v2(
                stage,
                system_prompt,
                user_payload,
                model_type,
                normalizer=normalizer,
                trace_run_id=trace_run_id,
            )

        usage_items: list[dict[str, Any]] = []
        validation_error: ValidationError | None = None
        format_error_code: str | None = None
        model_call_error_details: dict[str, Any] | None = None
        for attempt in range(2):
            current_payload = dict(user_payload)
            if validation_error is not None:
                current_payload["repair_feedback"] = {
                    "reason_code": f"{stage}_MODEL_OUTPUT_INVALID",
                    "validation_errors": _serializable_validation_errors(
                        validation_error
                    ),
                    "instruction": "只修复字段结构和类型，保持原问题语义不变。",
                }
            elif format_error_code is not None:
                current_payload["repair_feedback"] = {
                    "reason_code": format_error_code,
                    "instruction": "上一次输出不是合法 JSON 对象。只输出符合原契约的 JSON，保持原问题语义不变。",
                }
            user_prompt = orjson.dumps(current_payload).decode()
            try:
                with self._trace_node(
                    trace_run_id,
                    TraceNodeType.LLM,
                    stage.lower(),
                    _stage_display_name(stage, attempt),
                    input_data={
                        "stage": stage,
                        "attempt": attempt + 1,
                        "repair": validation_error is not None
                        or format_error_code is not None,
                    },
                    input_detail={
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    },
                    attributes=llm_attributes(
                        model=self._question_model_service.model_name
                    ),
                    metadata={"stage": stage, "attempt": attempt + 1},
                ) as model_node:
                    response = self._invoke_model(stage, system_prompt, user_prompt)
                    usage_items.append(response.usage_metadata)
                    payload = (
                        normalizer(response.payload)
                        if normalizer is not None
                        else response.payload
                    )
                    if model_node is not None:
                        model_node.set_token_usage(response.usage_metadata)
                        model_node.set_output_detail(
                            {
                                "raw_content": response.raw_content,
                                "parsed_payload": response.payload,
                                "normalized_payload": payload,
                            }
                        )
                    try:
                        validated = model_type.model_validate(payload)
                    except ValidationError as exc:
                        validation_error = exc
                        if model_node is not None:
                            model_node.set_status(TraceNodeStatus.REJECTED)
                            model_node.set_output(
                                {
                                    "stage": stage,
                                    "attempt": attempt + 1,
                                    "validation_status": "invalid",
                                    "validation_errors": _serializable_validation_errors(
                                        exc
                                    ),
                                }
                            )
                        if attempt == 1 and validation_fallback is not None:
                            try:
                                validated = model_type.model_validate(
                                    validation_fallback(payload)
                                )
                            except ValidationError as fallback_exc:
                                validation_error = fallback_exc
                            else:
                                if model_node is not None:
                                    model_node.set_status(TraceNodeStatus.SUCCEEDED)
                                    model_node.set_output(
                                        {
                                            "stage": stage,
                                            "attempt": attempt + 1,
                                            "validation_status": "fallback_repaired",
                                        }
                                    )
                                return validated, _merge_usage(*usage_items)
                    else:
                        if model_node is not None:
                            model_node.set_output(
                                {
                                    "stage": stage,
                                    "attempt": attempt + 1,
                                    "validation_status": "valid",
                                }
                            )
                        return validated, _merge_usage(*usage_items)
            except QuestionUnderstandingError as model_error:
                # 修复调用失败时保留第一次精确的结构校验错误，避免错误原因被覆盖。
                if model_error.details:
                    model_call_error_details = model_error.details
                if model_node is not None and model_error.details:
                    model_node.set_output(
                        {
                            "stage": stage,
                            "attempt": attempt + 1,
                            "error_code": str(model_error),
                            "error_details": model_error.details,
                        }
                    )
                    model_node.set_output_detail(
                        {"error_details": model_error.details}
                    )
                if validation_error is not None:
                    break
                error_code = str(model_error)
                repairable_format_errors = {
                    f"{stage}_MODEL_OUTPUT_NOT_JSON",
                    f"{stage}_MODEL_OUTPUT_NOT_OBJECT",
                }
                if error_code in repairable_format_errors and attempt == 0:
                    format_error_code = error_code
                    continue
                raise
        if validation_error is None:
            raise QuestionUnderstandingError(
                format_error_code or f"{stage}_MODEL_OUTPUT_INVALID",
                details=model_call_error_details,
            )
        raise QuestionUnderstandingError(
            f"{stage}_MODEL_OUTPUT_INVALID: {validation_error}",
            details=model_call_error_details,
        ) from validation_error

    def _invoke_validated_model_v2(
        self,
        stage: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        model_type: type[ModelType],
        *,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None,
        trace_run_id: int | None,
    ) -> tuple[ModelType, dict[str, int]]:
        """执行 R0 的归一化→校验→字段补丁→不变量流水线。"""

        usage_items: list[dict[str, Any]] = []
        with self._trace_node(
            trace_run_id,
            TraceNodeType.LLM,
            stage.lower(),
            _stage_display_name(stage, 0),
            input_data={"stage": stage, "attempt": 1, "repair": False},
            input_detail={
                "system_prompt": system_prompt,
                "user_prompt": orjson.dumps(user_payload).decode(),
            },
            attributes=llm_attributes(
                model=self._question_model_service.model_name
            ),
            metadata={"stage": stage, "attempt": 1},
        ) as model_node:
            response = self._invoke_model(
                stage,
                system_prompt,
                orjson.dumps(user_payload).decode(),
            )
            usage_items.append(response.usage_metadata)
            normalized_result = normalize_model_payload(
                response.payload,
                stage=stage,
                model_type=model_type,
                temporal_authority_enabled=self._temporal_authority_enabled,
            )
            payload = (
                normalizer(normalized_result.payload)
                if normalizer is not None
                else normalized_result.payload
            )
            if model_node is not None:
                model_node.set_token_usage(response.usage_metadata)
                model_node.set_output_detail(
                    {
                        "raw_content": response.raw_content,
                        "parsed_payload": response.payload,
                        "normalized_payload": payload,
                        "dropped_fields": list(normalized_result.dropped_fields),
                    }
                )
            try:
                validated = model_type.model_validate(payload)
            except ValidationError as validation_error:
                if model_node is not None:
                    model_node.set_status(TraceNodeStatus.REJECTED)
                    model_node.set_output(
                        {
                            "stage": stage,
                            "attempt": 1,
                            "validation_status": "invalid",
                            "validation_errors": _serializable_validation_errors(
                                validation_error
                            ),
                            "dropped_fields": list(normalized_result.dropped_fields),
                        }
                    )
                patched_payload = self._request_field_patch(
                    stage=stage,
                    payload=payload,
                    model_type=model_type,
                    validation_error=validation_error,
                    trace_run_id=trace_run_id,
                )
                try:
                    assert_repair_invariants(payload, patched_payload, stage=stage)
                except ValueError as invariant_error:
                    # 语义不变量失败必须归因到问题理解修复协议，不能向上冒泡成通用异常。
                    raise QuestionUnderstandingError(
                        f"{stage}_SEMANTIC_REPAIR_INVARIANT_VIOLATION",
                        details={
                            "repair_protocol": "field_patch",
                            "error": str(invariant_error),
                        },
                    ) from invariant_error
                try:
                    repaired = model_type.model_validate(patched_payload)
                except ValidationError as repaired_error:
                    raise QuestionUnderstandingError(
                        f"{stage}_MODEL_OUTPUT_INVALID",
                        details={
                            "validation_errors": _serializable_validation_errors(
                                repaired_error
                            ),
                            "repair_protocol": "field_patch",
                        },
                    ) from repaired_error
                if model_node is not None:
                    model_node.set_status(TraceNodeStatus.SUCCEEDED)
                    model_node.set_output(
                        {
                            "stage": stage,
                            "attempt": 1,
                            "validation_status": "field_patch_repaired",
                            "dropped_fields": list(normalized_result.dropped_fields),
                        }
                    )
                return repaired, _merge_usage(*usage_items)
            else:
                if model_node is not None:
                    model_node.set_output(
                        {
                            "stage": stage,
                            "attempt": 1,
                            "validation_status": "valid",
                            "dropped_fields": list(normalized_result.dropped_fields),
                        }
                    )
                return validated, _merge_usage(*usage_items)

    def _request_field_patch(
        self,
        *,
        stage: str,
        payload: dict[str, Any],
        model_type: type[ModelType],
        validation_error: ValidationError,
        trace_run_id: int | None,
    ) -> dict[str, Any]:
        """请求一次只修改失败路径的补丁，不重新生成完整语义对象。"""

        patch_request = build_patch_request(
            stage=stage,
            payload=payload,
            validation_error=validation_error,
            model_type=model_type,
        )
        patch_system_prompt = (
            "你是结构化字段补丁器。只输出 JSON："
            '{"patches":{"字段路径": "修复后的值"}}。'
            "只修改 failed_fields 中列出的路径，不得新增、删除或改写其他语义字段；"
            "不要输出 Markdown、解释或完整对象。"
        )
        patch_payload = {
            "repair_feedback": patch_request,
        }
        try:
            response = self._invoke_model(
                f"{stage}_FIELD_PATCH",
                patch_system_prompt,
                orjson.dumps(patch_payload).decode(),
            )
            patches = parse_patch_payload(response.payload)
            return apply_field_patches(payload, patches)
        except (QuestionUnderstandingError, ValueError) as exc:
            raise QuestionUnderstandingError(
                f"{stage}_FIELD_PATCH_FAILED",
                details={
                    "repair_protocol": "field_patch",
                    "error": str(exc),
                },
            ) from exc

    def _invoke_model(
        self,
        stage: str,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResult:
        try:
            return self._question_model_service.invoke(
                QuestionModelInvocationData(
                    stage=stage,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            )
        except QuestionModelCallError as exc:
            raise QuestionUnderstandingError(
                f"{stage}_MODEL_CALL_FAILED",
                details=exc.details,
            ) from exc
        except QuestionModelOutputError as exc:
            if exc.code == "QUESTION_MODEL_EMPTY_RESPONSE":
                raise QuestionUnderstandingError(
                    "QUESTION_UNDERSTANDING_MODEL_EMPTY_RESPONSE"
                ) from exc
            if exc.code == "QUESTION_MODEL_OUTPUT_NOT_OBJECT":
                raise QuestionUnderstandingError(
                    f"{stage}_MODEL_OUTPUT_NOT_OBJECT"
                ) from exc
            raise QuestionUnderstandingError(f"{stage}_MODEL_OUTPUT_NOT_JSON") from exc
        except QuestionModelError as exc:
            raise QuestionUnderstandingError(
                f"{stage}_MODEL_INVOCATION_INVALID"
            ) from exc

    @contextmanager
    def _trace_node(
        self,
        run_id: int | None,
        node_type: TraceNodeType,
        name: str,
        display_name: str,
        *,
        input_data: dict[str, Any] | None = None,
        input_detail: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceNodeHandle | None]:
        if run_id is None or run_id <= 0:
            yield None
            return
        with self._trace_recorder.node(
            TraceNodeSpec(
                run_id=run_id,
                node_type=node_type,
                name=name,
                display_name=display_name,
                attributes=attributes or {},
                metadata=metadata or {},
            ),
            input_data=input_data,
            input_detail=input_detail,
        ) as node:
            yield node


def apply_question_understanding_clarification(
    *,
    understanding: dict[str, Any],
    resume_payload: dict[str, Any],
    answer: dict[str, Any],
    temporal_context: TemporalContext,
) -> QuestionUnderstandingOutput:
    """将用户回答定点写回既有问题理解，不重新调用问题理解模型。"""

    try:
        previous = QuestionUnderstandingOutput.model_validate(understanding)
    except ValidationError as exc:
        raise QuestionUnderstandingError(
            f"CLARIFICATION_CHECKPOINT_INVALID: {exc}"
        ) from exc

    operation = str(resume_payload.get("operation") or "")
    if operation not in _CLARIFICATION_OPERATIONS:
        raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")

    intent = previous.intent
    if operation == "set_dimension_role":
        slot_name = str(resume_payload.get("slot_name") or "").strip()
        if not slot_name:
            raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")
        intent = _apply_dimension_role_selection(intent, slot_name, answer)
    elif operation == "set_dimension_filter_value":
        slot_name = str(resume_payload.get("slot_name") or "").strip()
        if not slot_name:
            raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")
        intent = _apply_dimension_filter_value(intent, slot_name, answer)
    elif operation == "set_intent_type":
        intent = _apply_intent_type_selection(intent, answer)
    elif operation == "set_order_direction":
        intent = _apply_order_direction_selection(intent, answer)
    elif operation == "set_ranking_limit":
        intent = _apply_ranking_limit_selection(intent, answer)
    elif operation == "set_grouping":
        intent = _apply_grouping_selection(intent, answer)
    elif operation == "set_time_range_raw":
        intent = _apply_time_range_raw_input(intent, answer)
    else:  # pragma: no cover - 操作集合已在上方校验。
        raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")

    updated_intent = _stabilize_intent(
        intent,
        temporal_context,
        # 恢复问题理解澄清时同样不在前置阶段解析时间，避免绕过 Agent 时间工具。
        use_legacy_time_interpretation=False,
    )
    # 重写结果已经在挂起前确定；这里只重新执行无模型副作用的业务校验。
    validation = _validate_understanding(
        updated_intent,
        temporal_interpretation=previous.temporal_interpretation,
    )
    return previous.model_copy(
        update={"intent": updated_intent, "validation": validation}
    )


_CLARIFICATION_OPERATIONS = frozenset(
    {
        "set_dimension_role",
        "set_dimension_filter_value",
        "set_intent_type",
        "set_order_direction",
        "set_ranking_limit",
        "set_grouping",
        "set_time_range_raw",
    }
)

_CLARIFIABLE_INTENT_TYPES = frozenset(
    {
        "metric_query",
        "trend_analysis",
        "ranking_analysis",
        "comparison_analysis",
        "detail_query",
        "share_analysis",
    }
)


def _apply_dimension_role_selection(
    intent: IntentRecognitionOutput,
    slot_name: str,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    slots = list(intent.dimension_slots)
    matching_indexes = [
        index for index, slot in enumerate(slots) if slot.name == slot_name
    ]
    if len(matching_indexes) != 1:
        raise QuestionUnderstandingError("CLARIFICATION_DIMENSION_SLOT_NOT_FOUND")
    slot_index = matching_indexes[0]
    slot = slots[slot_index]
    ambiguous_slots = [
        item
        for item in intent.ambiguous_slots
        if item not in {slot_name, "dimension", "filter_value"}
    ]

    selected_value = _single_clarification_selection(answer)
    expected_values = {
        f"group_by:{slot_name}": "group_by",
        f"filter:{slot_name}": "filter",
        f"ignore:{slot_name}": "ignore",
    }
    selected_role = expected_values.get(selected_value)
    if selected_role is None:
        raise QuestionUnderstandingError("CLARIFICATION_DIMENSION_ROLE_INVALID")
    if selected_role == "ignore":
        slots.pop(slot_index)
        dimension_mentions = [
            item for item in intent.dimension_mentions if item != slot_name
        ]
    else:
        slots[slot_index] = slot.model_copy(
            update={
                "role": selected_role,
                "value": None,
                "value_status": "not_provided",
                "value_confidence": 0.0,
            }
        )
        dimension_mentions = intent.dimension_mentions

    query_shape = intent.query_shape
    # 这里不重新解释自然语言，只把用户已经确认的维度角色同步到查询组织方式。
    has_group_by_slot = any(item.role == "group_by" for item in slots)
    has_comparison_values = (
        intent.intent_type in {"comparison_analysis", "share_analysis"}
        and any(
            item.role == "filter"
            and isinstance(item.value, list)
            and len(item.value) >= 2
            for item in slots
        )
    )
    query_shape = query_shape.model_copy(
        update={
            "needs_group_by": bool(
                has_group_by_slot
                or has_comparison_values
                or query_shape.time_grain is not None
            )
        }
    )
    return intent.model_copy(
        update={
            "dimension_mentions": dimension_mentions,
            "dimension_slots": slots,
            "ambiguous_slots": ambiguous_slots,
            "query_shape": query_shape,
        }
    )


def _apply_dimension_filter_value(
    intent: IntentRecognitionOutput,
    slot_name: str,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    slots = list(intent.dimension_slots)
    matching_indexes = [
        index for index, slot in enumerate(slots) if slot.name == slot_name
    ]
    if len(matching_indexes) != 1:
        raise QuestionUnderstandingError("CLARIFICATION_DIMENSION_SLOT_NOT_FOUND")
    filter_value = _clarification_answer_value(answer)
    slots[matching_indexes[0]] = slots[matching_indexes[0]].model_copy(
        update={
            "role": "filter",
            "value": filter_value,
            "value_status": "provided",
            "value_confidence": 1.0,
        }
    )
    return intent.model_copy(update={"dimension_slots": slots})


def _apply_intent_type_selection(
    intent: IntentRecognitionOutput,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    selected_value = _single_clarification_selection(answer)
    intent_type = (
        selected_value.split(":", 1)[1].strip()
        if ":" in selected_value
        else selected_value.strip()
    )
    if intent_type not in _CLARIFIABLE_INTENT_TYPES:
        raise QuestionUnderstandingError("CLARIFICATION_INTENT_TYPE_INVALID")
    select_mode = (
        "detail"
        if intent_type == "detail_query"
        or (
            intent_type == "ranking_analysis"
            and intent.query_shape.select_mode == "detail"
        )
        else "aggregate"
    )
    query_shape = intent.query_shape.model_copy(update={"select_mode": select_mode})
    conflict_slots = [
        slot
        for slot in intent.conflict_slots
        if slot not in {"intent", "select_mode", "意图"}
    ]
    return intent.model_copy(
        update={
            "intent_type": intent_type,
            "query_shape": query_shape,
            "conflict_slots": conflict_slots,
        }
    )


def _apply_order_direction_selection(
    intent: IntentRecognitionOutput,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    selected_value = _single_clarification_selection(answer)
    direction = (
        selected_value.split(":", 1)[1].strip()
        if ":" in selected_value
        else selected_value.strip()
    )
    if direction not in {"asc", "desc", "none"}:
        raise QuestionUnderstandingError("CLARIFICATION_ORDER_DIRECTION_INVALID")
    ranking = intent.ranking
    if direction == "none":
        query_shape = intent.query_shape.model_copy(
            update={
                "order_direction": None,
                "needs_order_by": False,
                "limit": None,
            }
        )
        return intent.model_copy(update={"query_shape": query_shape})
    query_shape = intent.query_shape.model_copy(
        update={"order_direction": direction, "needs_order_by": True}
    )
    if ranking is not None:
        ranking = ranking.model_copy(update={"direction": direction})
    return intent.model_copy(update={"query_shape": query_shape, "ranking": ranking})


def _apply_ranking_limit_selection(
    intent: IntentRecognitionOutput,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    selected_value = _single_clarification_selection(answer)
    raw_limit = (
        selected_value.split(":", 1)[1].strip()
        if ":" in selected_value
        else selected_value.strip()
    )
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise QuestionUnderstandingError("CLARIFICATION_RANKING_LIMIT_INVALID") from exc
    if not 1 <= limit <= 1000:
        raise QuestionUnderstandingError("CLARIFICATION_RANKING_LIMIT_INVALID")
    query_shape = intent.query_shape.model_copy(update={"limit": limit})
    ranking = intent.ranking
    if ranking is not None:
        ranking = ranking.model_copy(update={"limit": limit})
    return intent.model_copy(update={"query_shape": query_shape, "ranking": ranking})


def _apply_grouping_selection(
    intent: IntentRecognitionOutput,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    selected_value = _single_clarification_selection(answer)
    selection = (
        selected_value.split(":", 1)[1].strip()
        if ":" in selected_value
        else selected_value.strip()
    )
    if selection not in {"enable", "disable"}:
        raise QuestionUnderstandingError("CLARIFICATION_GROUPING_SELECTION_INVALID")
    if selection == "enable":
        query_shape = intent.query_shape.model_copy(update={"needs_group_by": True})
        return intent.model_copy(update={"query_shape": query_shape})
    # 用户明确不需要分组时，同步降级分组槽位和粒度，避免校验继续报冲突。
    slots = [
        (
            slot.model_copy(update={"role": "display"})
            if slot.role == "group_by"
            else slot
        )
        for slot in intent.dimension_slots
    ]
    query_shape = intent.query_shape.model_copy(
        update={"needs_group_by": False, "time_grain": None}
    )
    return intent.model_copy(
        update={"dimension_slots": slots, "query_shape": query_shape}
    )


def _apply_time_range_raw_input(
    intent: IntentRecognitionOutput,
    answer: dict[str, Any],
) -> IntentRecognitionOutput:
    raw_time = _clarification_answer_value(answer)
    if not raw_time:
        raise QuestionUnderstandingError("CLARIFICATION_TIME_RANGE_REQUIRED")
    time_range = TimeRange(raw=raw_time, value_status="provided")
    time_mentions = list(intent.time_mentions)
    if raw_time not in time_mentions:
        time_mentions.append(raw_time)
    return intent.model_copy(
        update={"time_range": time_range, "time_mentions": time_mentions}
    )


def _normalize_question_category(value: Any) -> str:
    """分诊值宽松归一；不可识别时保守回落为 data_query，不阻断主链路。"""

    aliases = {
        "chat": "chitchat",
        "smalltalk": "chitchat",
        "small_talk": "chitchat",
        "greeting": "chitchat",
        "data": "data_query",
        "query": "data_query",
        "meta": "meta_query",
        "meta_query": "meta_query",
        "capability": "meta_query",
        "out_of_scope": "out_of_scope",
        "outscope": "out_of_scope",
        "forbidden": "out_of_scope",
    }
    key = "".join(str(value or "").strip().lower().split())
    return aliases.get(key, "data_query")


def _build_rewrite_context(context: dict[str, Any]) -> dict[str, Any]:
    """构造问题重写允许读取的自然语言上下文。

    重写阶段不能读取上一轮的结构化意图、资产绑定、SQL 或 AnalysisPlan；
    这些内容属于后续执行阶段，直接注入会让重写器越过职责边界。
    """

    result: dict[str, Any] = {}
    last_question = context.get("last_rewritten_question")
    if isinstance(last_question, str) and last_question.strip():
        result["last_rewritten_question"] = last_question.strip()

    history = context.get("history")
    if isinstance(history, list):
        safe_history: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            safe_item: dict[str, str] = {}
            for key in ("question", "answer_brief"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    safe_item[key] = value.strip()
            if safe_item:
                safe_history.append(safe_item)
        if safe_history:
            result["history"] = safe_history

    for key in (
        "pending_question",
        "clarification_question",
        "user_confirmation",
    ):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _normalize_question_rewrite_payload(
    payload: dict[str, Any],
    *,
    original_question: str,
) -> dict[str, Any]:
    """将迁移期旧重写字段映射到新契约，不保留旧扩展字段。"""

    normalized = dict(payload)
    if "rewrite_question" not in normalized and "rewritten_question" in normalized:
        normalized["rewrite_question"] = normalized.pop("rewritten_question")
        # 旧模型没有输出原问题字段，该字段由服务端保存的原始输入补齐。
        normalized["original_question"] = original_question
    return {
        key: normalized[key]
        for key in ("original_question", "rewrite_question")
        if key in normalized
    }


def _derive_legacy_message_type(
    original_question: str,
    rewrite_question: str,
    context: dict[str, Any],
) -> str:
    """为尚未迁移的完整问题理解结果保留只读兼容字段。

    该字段不参与问题重写模型输出、语义解析或模式路由；新代码不应依赖它。
    """

    if context.get("pending_question") or context.get("clarification_question"):
        return "clarification_reply"
    if (
        isinstance(context.get("last_rewritten_question"), str)
        and rewrite_question.strip() != original_question.strip()
    ):
        return "followup"
    return "new_question"


def _validate_understanding(
    intent: IntentRecognitionOutput,
    *,
    temporal_interpretation: TemporalInterpretationResult | None = None,
    pending_binding_enabled: bool = False,
) -> IntentValidationOutput:
    result = validate_question_understanding(
        QuestionUnderstandingValidationData(
            # 重写阶段不输出缺失槽位；上下文歧义由统一理解和确定性校验处理。
            rewrite_need_user_input=False,
            rewrite_missing_slots=(),
            intent_type=intent.intent_type,
            metric_mentions=tuple(intent.metric_mentions),
            dimension_slots=tuple(
                slot.model_dump(mode="json") for slot in intent.dimension_slots
            ),
            time_range=intent.time_range.model_dump(mode="json"),
            time_ranges=tuple(
                item.model_dump(mode="json") for item in intent.time_ranges
            ),
            comparison=(
                intent.comparison.model_dump(mode="json")
                if intent.comparison is not None
                else {}
            ),
            composition=(
                intent.composition.model_dump(mode="json")
                if intent.composition is not None
                else {}
            ),
            multi_step=(
                intent.multi_step.model_dump(mode="json")
                if intent.multi_step is not None
                else {}
            ),
            query_shape=intent.query_shape.model_dump(mode="json"),
            ranking=(
                intent.ranking.model_dump(mode="json")
                if intent.ranking is not None
                else {}
            ),
            ambiguous_slots=tuple(intent.ambiguous_slots),
            conflict_slots=tuple(intent.conflict_slots),
            temporal_plan=(
                temporal_interpretation.plan
                if temporal_interpretation is not None
                else None
            ),
            pending_binding_enabled=pending_binding_enabled,
        )
    )
    return IntentValidationOutput(
        status="clarification_required" if result.issues else "valid",
        reason_codes=result.reason_codes,
        clarification_slots=result.clarification_slots,
    )


def _reconcile_detail_display_dimensions(
    intent: IntentRecognitionOutput,
    available_dimensions: list[dict[str, Any]],
    *,
    rewritten_question: str,
) -> IntentRecognitionOutput:
    """把明细查询中误放入指标列表的维度字段纠正为展示维度。"""

    if intent.intent_type != "detail_query":
        return intent

    candidates = normalize_dimension_candidates(available_dimensions)
    candidate_by_text = dimension_candidate_by_text(candidates, include_time=False)
    existing_names = {
        dimension_text_key(slot.name) for slot in intent.dimension_slots
    }
    metric_mentions: list[str] = []
    dimension_mentions = list(intent.dimension_mentions)
    dimension_slots = list(intent.dimension_slots)
    changed = False

    for mention in intent.metric_mentions:
        candidate = candidate_by_text.get(dimension_text_key(mention))
        if (
            candidate is None
            or dimension_text_key(candidate["name"]) in existing_names
            or _dimension_is_metric_modifier(candidate, rewritten_question, [mention])
        ):
            metric_mentions.append(mention)
            continue

        dimension_slots.append(
            DimensionSlot(
                name=candidate["name"],
                role="display",
                value=None,
                value_status="not_provided",
                value_confidence=1.0,
            )
        )
        dimension_mentions.append(candidate["name"])
        existing_names.add(dimension_text_key(candidate["name"]))
        changed = True

    if not changed:
        return intent

    return intent.model_copy(
        update={
            "metric_mentions": _unique_strings(metric_mentions),
            "dimension_mentions": _unique_strings(dimension_mentions),
            "dimension_slots": dimension_slots,
        }
    )


def _single_clarification_selection(answer: dict[str, Any]) -> str:
    selections = answer.get("selections")
    values = [
        str(item.get("value") or "").strip()
        for item in selections or []
        if isinstance(item, dict) and str(item.get("value") or "").strip()
    ]
    if len(values) != 1:
        raise QuestionUnderstandingError("CLARIFICATION_SINGLE_SELECTION_REQUIRED")
    return values[0]


def _clarification_answer_value(answer: dict[str, Any]) -> str:
    text = str(answer.get("text") or "").strip()
    if text:
        return text
    return _single_clarification_selection(answer)


def _temporal_clarification_answer(
    answer: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """区分结构化时间选项和自由文本回答。"""

    text = str(answer.get("text") or "").strip()
    if text:
        return text, None
    selections = answer.get("selections")
    selected = [item for item in selections or [] if isinstance(item, dict)]
    if len(selected) != 1:
        raise QuestionUnderstandingError("CLARIFICATION_SINGLE_SELECTION_REQUIRED")
    value = selected[0].get("value")
    if not isinstance(value, dict):
        confirmation = str(value or "").strip()
        if not confirmation:
            raise QuestionUnderstandingError("CLARIFICATION_SINGLE_SELECTION_REQUIRED")
        return confirmation, None
    confirmation = str(value.get("temporal_confirmation") or "").strip()
    plan = value.get("temporal_plan")
    if not confirmation or not isinstance(plan, dict):
        raise QuestionUnderstandingError("TEMPORAL_CONFIRMATION_PAYLOAD_INVALID")
    return confirmation, plan


ModelType = TypeVar("ModelType", bound=BaseModel)


def _stage_display_name(stage: str, attempt: int) -> str:
    names = {
        "QUESTION_REWRITE": "问题重写模型",
        "INTENT_RECOGNITION": "意图识别模型",
        "DIMENSION_RECOGNITION": "维度识别模型",
        "QUESTION_UNDERSTANDING": "统一问题理解模型",
    }
    base = names.get(stage, stage)
    return base if attempt == 0 else f"{base}（格式修复 {attempt}）"


def _serializable_validation_errors(
    validation_error: ValidationError,
) -> list[dict[str, Any]]:
    """只保留修复所需字段，避免校验上下文中的异常对象破坏 JSON 序列化。"""

    return [
        {
            "type": error.get("type"),
            "loc": list(error.get("loc") or ()),
            "msg": error.get("msg"),
        }
        for error in validation_error.errors(
            include_url=False,
            include_input=False,
        )
    ]


def _normalize_dimension_payload(
    payload: dict[str, Any],
    available_dimensions: list[dict[str, Any]],
    *,
    rewritten_question: str,
    metric_mentions: list[str],
    infer_from_question: bool = False,
    allow_time_dimensions: bool = False,
) -> dict[str, Any]:
    """统一维度候选、槽位和歧义结构，让业务不变量只在这里表达。"""

    normalized = dict(payload)
    legacy_filters = normalized.pop("filter_mentions", None)
    if "residual_filter_mentions" not in normalized and legacy_filters is not None:
        normalized["residual_filter_mentions"] = legacy_filters

    candidates = normalize_dimension_candidates(available_dimensions)
    candidate_by_text = dimension_candidate_by_text(candidates, include_time=False)
    candidate_by_text_with_time = dimension_candidate_by_text(
        candidates,
        include_time=True,
    )

    slots: list[Any] = []
    for raw_slot in normalized.get("dimension_slots") or []:
        if not isinstance(raw_slot, dict):
            slots.append(raw_slot)
            continue
        slot = dict(raw_slot)
        raw_name = str(slot.get("name") or slot.get("dimension") or "").strip()
        name_key = dimension_text_key(raw_name)
        if (
            name_key in candidate_by_text_with_time
            and name_key not in candidate_by_text
            and not allow_time_dimensions
        ):
            continue
        candidate = _resolve_dimension_candidate(
            raw_name,
            slot.get("value"),
            candidates,
        )
        if (
            candidate is not None
            and slot.get("value") in (None, "")
            and _dimension_is_metric_modifier(
                candidate,
                rewritten_question,
                metric_mentions,
            )
        ):
            # “客户当日GMV”中的“客户”属于指标修饰，不应凭空增加客户维度。
            continue
        if candidate is not None:
            slot["name"] = candidate["name"]
            slot.pop("dimension", None)
            slot["value"] = _normalize_dimension_value(slot.get("value"), candidate)
            matched_values = (
                _candidate_values_in_question(candidate, rewritten_question)
                if infer_from_question
                else []
            )
            if slot.get("value") in (None, "") and matched_values:
                # 已知枚举值在问题中明确出现时，直接补到同一个规范槽位，
                # 避免“交易渠道”和带示例的语义名称形成一有值、一歧义的重复槽位。
                slot.update(
                    {
                        "role": "filter",
                        "value": (
                            matched_values[0]
                            if len(matched_values) == 1
                            else matched_values
                        ),
                        "value_status": "provided",
                        "value_confidence": 1.0,
                    }
                )
        slots.append(slot)

    existing_slot_names = {
        str(slot.get("name") or "").strip() for slot in slots if isinstance(slot, dict)
    }
    if infer_from_question:
        for candidate in candidates:
            if candidate.get("is_time") or candidate["name"] in existing_slot_names:
                continue
            matched_values = _candidate_values_in_question(
                candidate,
                rewritten_question,
            )
            if not matched_values:
                continue
            slots.append(
                {
                    "name": candidate["name"],
                    "role": "filter",
                    "value": (
                        matched_values[0]
                        if len(matched_values) == 1
                        else matched_values
                    ),
                    "value_status": "provided",
                    "value_confidence": 1.0,
                }
            )
            existing_slot_names.add(candidate["name"])

    raw_resolved_mentions: list[str] = []
    raw_mentions = normalized.get("dimension_mentions")
    if isinstance(raw_mentions, list):
        for raw_mention in raw_mentions:
            mention = str(raw_mention or "").strip()
            mention_key = dimension_text_key(mention)
            if (
                mention_key in candidate_by_text_with_time
                and mention_key not in candidate_by_text
                and not allow_time_dimensions
            ):
                continue
            candidate = _resolve_dimension_candidate(mention, None, candidates)
            if candidate is not None and _dimension_is_metric_modifier(
                candidate,
                rewritten_question,
                metric_mentions,
            ):
                continue
            resolved = str(candidate["name"] if candidate is not None else mention)
            if resolved and resolved not in raw_resolved_mentions:
                raw_resolved_mentions.append(resolved)

    slot_names = {
        str(slot.get("name") or "").strip() for slot in slots if isinstance(slot, dict)
    }
    # 首次不替模型补槽位，让修复重试仍有机会返回完整值；重试仍失败时再走确定性收敛。
    mentions: list[str] = list(raw_resolved_mentions)
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        name = str(slot.get("name") or "").strip()
        if name and name not in mentions:
            mentions.append(name)
    normalized["dimension_slots"] = slots
    normalized["dimension_mentions"] = mentions
    normalized["ambiguous_slots"] = _normalize_dimension_ambiguities(
        normalized.get("ambiguous_slots"),
        candidates,
        slot_names,
        rewritten_question,
        metric_mentions,
    )

    slot_filter_keys = {
        (
            str(slot.get("name") or ""),
            str(slot.get("value") or ""),
        )
        for slot in slots
        if isinstance(slot, dict) and str(slot.get("role") or "").lower() == "filter"
    }
    residual_filters: list[Any] = []
    for raw_filter in normalized.get("residual_filter_mentions") or []:
        if not isinstance(raw_filter, dict):
            residual_filters.append(raw_filter)
            continue
        filter_item = dict(raw_filter)
        raw_name = str(
            filter_item.get("name")
            or filter_item.get("dimension")
            or filter_item.get("field")
            or ""
        ).strip()
        candidate = candidate_by_text.get(dimension_text_key(raw_name))
        if candidate is not None:
            filter_item["name"] = candidate["name"]
            filter_item.pop("dimension", None)
            filter_item.pop("field", None)
            filter_item["value"] = _normalize_dimension_value(
                filter_item.get("value"),
                candidate,
            )
        key = (
            str(filter_item.get("name") or ""),
            str(filter_item.get("value") or ""),
        )
        if key in slot_filter_keys:
            continue
        residual_filters.append(filter_item)
    normalized["residual_filter_mentions"] = residual_filters
    return normalized


def _normalize_unified_payload(
    payload: dict[str, Any],
    available_dimensions: list[dict[str, Any]],
    *,
    rewritten_question: str,
) -> dict[str, Any]:
    """只做统一输出的字段规范化，不从原问题补造缺失语义。"""

    payload = _normalize_comparison_payload(payload)
    metrics = [
        str(item).strip()
        for item in payload.get("metric_mentions") or []
        if str(item).strip()
    ]
    normalized = _normalize_dimension_payload(
        payload,
        available_dimensions,
        rewritten_question=rewritten_question,
        metric_mentions=metrics,
        infer_from_question=False,
        allow_time_dimensions=True,
    )
    normalized["category"] = _normalize_question_category(payload.get("category"))
    residual_filters = normalized.pop("residual_filter_mentions", None)
    if "filter_mentions" not in normalized and residual_filters is not None:
        normalized["filter_mentions"] = residual_filters

    ranking = normalized.get("ranking")
    if isinstance(ranking, dict):
        # ranking 是统一模型的主结构，query_shape 仅保留给现有下游兼容。
        target = ranking.get("target")
        if target:
            candidate = _resolve_dimension_candidate(
                str(target),
                None,
                normalize_dimension_candidates(available_dimensions),
            )
            if candidate is not None:
                ranking["target"] = candidate["name"]
        query_shape = dict(normalized.get("query_shape") or {})
        if ranking.get("direction") in {"asc", "desc"}:
            query_shape["order_direction"] = ranking["direction"]
            query_shape["needs_order_by"] = True
        if ranking.get("limit") is not None:
            query_shape["limit"] = ranking["limit"]
        if ranking.get("target"):
            query_shape["needs_group_by"] = True
        if str(normalized.get("intent_type") or "") == "ranking_analysis":
            query_shape.setdefault("select_mode", "aggregate")
            query_shape["needs_group_by"] = True
            query_shape["needs_order_by"] = True
        normalized["query_shape"] = query_shape

    normalized.pop("repair_feedback", None)
    return normalized


def _normalize_comparison_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容旧模型的比较字段，并把趋势环比转换为查询形状。"""

    normalized = dict(payload)
    query_shape = dict(normalized.get("query_shape") or {})
    comparison_type_aliases = {
        "year_over_year": "yoy",
        "year-over-year": "yoy",
        "同比": "yoy",
        "month_over_month": "mom",
        "month-over-month": "mom",
        "环比": "mom",
        "difference": "custom",
        "growth": "custom",
        "percent_change": "custom",
        "percentage_change": "custom",
        "change_rate": "custom",
    }
    shape_method = str(query_shape.get("comparison_type") or "").strip().lower()
    if shape_method in comparison_type_aliases:
        query_shape["comparison_type"] = comparison_type_aliases[shape_method]
    comparison = normalized.get("comparison")
    if not isinstance(comparison, dict):
        if normalized.get("intent_type") == "comparison_analysis":
            ranges = [
                item
                for item in normalized.get("time_ranges") or []
                if isinstance(item, dict) and item.get("raw")
            ]
            if len(ranges) >= 2:
                # 模型常把当前期放在前面；比较规范以最后一个时段为基期。
                comparison = {
                    "base": ranges[-1]["raw"],
                    "compare": [item["raw"] for item in ranges[:-1]],
                    # 用户已明确给出多个时段时，默认是自定义时段比较，
                    # 不应因为模型遗漏 method 而要求用户重复说明。
                    "method": query_shape.get("comparison_type") or "custom",
                }
            else:
                normalized["query_shape"] = query_shape
                return normalized
        else:
            normalized["query_shape"] = query_shape
            return normalized
    comparison = dict(comparison)
    if "base" not in comparison and comparison.get("base_time") is not None:
        comparison["base"] = comparison["base_time"]
    if "compare" not in comparison and comparison.get("compare_time") is not None:
        comparison["compare"] = [comparison["compare_time"]]
    if "compare" not in comparison and comparison.get("target") is not None:
        comparison["compare"] = [comparison["target"]]
    if (
        ("compare" not in comparison or not comparison.get("compare"))
        and comparison.get("base") is not None
    ):
        ranges = [
            item.get("raw")
            for item in normalized.get("time_ranges") or []
            if isinstance(item, dict) and item.get("raw")
        ]
        time_candidates = [item for item in ranges if item != comparison["base"]]
        if time_candidates:
            comparison["compare"] = time_candidates
        values = []
        for slot in normalized.get("dimension_slots") or []:
            if not isinstance(slot, dict) or not isinstance(slot.get("value"), list):
                continue
            values.extend(item for item in slot["value"] if item not in (None, ""))
        candidates = [item for item in values if item != comparison["base"]]
        if candidates and not comparison.get("compare"):
            comparison["compare"] = candidates
    method = comparison.get("method") or comparison.get("type") or query_shape.get("comparison_type")
    method = comparison_type_aliases.get(str(method or "").strip().lower(), method)
    if method in {"yoy", "mom", "custom"}:
        comparison["method"] = method
    comparison.pop("type", None)
    comparison.pop("base_time", None)
    comparison.pop("compare_time", None)
    # target 是旧输出中的冗余日期字段；base/compare 才是权威时段字段。
    comparison.pop("target", None)
    if isinstance(comparison.get("compare"), str):
        comparison["compare"] = [comparison["compare"]]
    if method in {"yoy", "mom", "custom"}:
        query_shape["comparison_type"] = method
    if (
        normalized.get("intent_type") == "metric_query"
        and query_shape.get("time_grain") is not None
        and not query_shape.get("needs_group_by")
    ):
        # “2026年6月”是筛选时段而不是按月分组。只有声明分组时才保留粒度。
        query_shape["time_grain"] = None
    if (
        normalized.get("intent_type") == "trend_analysis"
        and method in {"yoy", "mom"}
        and not {"base", "compare"} <= comparison.keys()
    ):
        query_shape["comparison_type"] = method
        normalized["query_shape"] = query_shape
        normalized["comparison"] = None
    else:
        normalized["comparison"] = comparison
    normalized["query_shape"] = query_shape
    return normalized


def _repair_dimension_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    """模型修复重试后仍漏槽位时，补成待澄清槽位并恢复一一对应不变量。"""

    repaired = dict(payload)
    slots = [
        dict(slot)
        for slot in repaired.get("dimension_slots") or []
        if isinstance(slot, dict) and str(slot.get("name") or "").strip()
    ]
    slot_names = {str(slot["name"]).strip() for slot in slots}
    for mention in repaired.get("dimension_mentions") or []:
        name = str(mention or "").strip()
        if not name or name in slot_names:
            continue
        slots.append(
            {
                "name": name,
                "role": "ambiguous",
                "value": None,
                "value_status": "not_provided",
                "value_confidence": 0.0,
            }
        )
        slot_names.add(name)
    repaired["dimension_slots"] = slots
    repaired["dimension_mentions"] = [str(slot["name"]).strip() for slot in slots]
    return repaired


def _resolve_dimension_candidate(
    raw_name: str,
    value: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """名称优先精确匹配；别名冲突时根据值是否像标识符选择 ID 维度。"""

    name_key = dimension_text_key(raw_name)
    exact = [
        item for item in candidates if dimension_text_key(item.get("name")) == name_key
    ]
    if exact:
        return exact[0]
    matched = [
        item
        for item in candidates
        if name_key
        and name_key
        in {dimension_text_key(alias) for alias in item.get("aliases") or []}
    ]
    if len(matched) <= 1:
        return matched[0] if matched else None
    if value not in (None, ""):
        identifier_matches = [
            item for item in matched if item.get("value_kind") == "numeric_id"
        ]
        if len(identifier_matches) == 1:
            return identifier_matches[0]
    return matched[0]


def _dimension_is_metric_modifier(
    candidate: dict[str, Any],
    question: str,
    metric_mentions: list[str],
) -> bool:
    """未提供值的维度词紧邻指标短语时，将其视为指标口径修饰。"""

    compact_question = dimension_text_key(question)
    names = [
        str(item).strip()
        for item in [candidate.get("name"), *(candidate.get("aliases") or [])]
        if str(item or "").strip()
    ]
    if any(
        re.search(
            rf"(?:按|各|每个|不同)\s*{re.escape(name)}",
            question,
            flags=re.IGNORECASE,
        )
        for name in names
    ):
        # 明确的分组表达优先于指标短语包含关系。
        return False
    return any(
        (
            dimension_text_key(name + metric) in compact_question
            or (
                dimension_text_key(candidate.get("name")) != dimension_text_key(metric)
                and dimension_text_key(name) != dimension_text_key(metric)
                and dimension_text_key(name) in dimension_text_key(metric)
                and dimension_text_key(metric) in compact_question
            )
        )
        for name in names
        for metric in metric_mentions
        if metric
    )


def _candidate_values_in_question(
    candidate: dict[str, Any],
    question: str,
) -> list[str]:
    """从语义维度名称中声明的枚举示例提取用户明确输入的值。"""

    name = str(candidate.get("name") or "")
    matched = re.search(r"(?:，|,)?\s*(?:例如|如)\s*(.+)$", name)
    if not matched:
        return []
    examples = [
        item.strip()
        for item in re.split(r"(?:或|、|/|，|,)", matched.group(1))
        if item.strip()
    ]
    compact_question = dimension_text_key(question)
    return [
        example
        for example in examples
        if dimension_text_key(example) in compact_question
    ]


def _normalize_dimension_ambiguities(
    value: Any,
    candidates: list[dict[str, Any]],
    slot_names: set[str],
    question: str,
    metric_mentions: list[str],
) -> list[str]:
    """兼容模型把 ambiguous_slots 输出成对象，并去除已由槽位状态表达的重复项。"""

    raw_items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            text = str(
                item.get("slot_type") or item.get("name") or item.get("dimension") or ""
            ).strip()
        else:
            text = str(item or "").strip()
        candidate = _resolve_dimension_candidate(text, None, candidates)
        if candidate is not None and _dimension_is_metric_modifier(
            candidate,
            question,
            metric_mentions,
        ):
            continue
        normalized = str(candidate["name"] if candidate is not None else text)
        if (
            normalized
            and normalized not in slot_names
            and normalized not in {"dimension", "filter_value", "维度"}
            and normalized not in result
        ):
            result.append(normalized)
    return result


def _normalize_dimension_value(
    value: Any,
    candidate: dict[str, Any],
) -> Any:
    """ID 类型只移除明确的维度名称和连接符，不猜测或改写业务值。"""

    if isinstance(value, list):
        return [_normalize_dimension_value(item, candidate) for item in value]
    if not isinstance(value, str) or candidate.get("value_kind") != "numeric_id":
        return value
    normalized = value.strip()
    names = sorted(
        {
            str(item).strip()
            for item in [candidate.get("name"), *(candidate.get("aliases") or [])]
            if str(item or "").strip()
        },
        key=len,
        reverse=True,
    )
    for name in names:
        normalized = re.sub(
            rf"^\s*{re.escape(name)}\s*(?:为|=|:|：)?\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            rf"\s*{re.escape(name)}\s*$",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized.strip()


def _stabilize_intent(
    intent: IntentRecognitionOutput,
    temporal_context: TemporalContext,
    *,
    use_legacy_time_interpretation: bool,
) -> IntentRecognitionOutput:
    """规范化无歧义数据，并从模型语义结果派生后续必需槽位。"""

    source_time_ranges = intent.time_ranges or (
        [intent.time_range]
        if intent.time_range.value_status == "provided"
        else []
    )
    time_ranges = [
        TimeRange.model_validate(
            normalize_time_range_payload(
                item.model_dump(mode="json"),
                temporal_context=temporal_context,
            )
        )
        if use_legacy_time_interpretation
        else item
        for item in source_time_ranges
    ]
    time_range = time_ranges[0] if time_ranges else TimeRange()
    metric_internal_time_mentions = {
        mention
        for mention in intent.time_mentions
        if mention != time_range.raw
        and any(
            mention != metric and mention in metric
            for metric in intent.metric_mentions
        )
    }
    time_mentions = [
        mention
        for mention in intent.time_mentions
        if mention not in metric_internal_time_mentions
    ]
    for item in time_ranges:
        if item.raw and item.raw not in time_mentions:
            time_mentions.append(item.raw)

    conflict_slots = list(intent.conflict_slots)
    if metric_internal_time_mentions and len(_unique_strings(time_mentions)) <= 1:
        # 移除指标内部修饰后只剩一个全局时间时，原时间冲突已经不存在。
        conflict_slots = [
            slot
            for slot in conflict_slots
            if not _is_metric_internal_time_conflict(
                slot,
                metric_internal_time_mentions,
            )
        ]

    dimension_slots = list(intent.dimension_slots)

    # 必需槽位只由服务端根据稳定后的意图派生，不继承模型或澄清前的陈旧结果。
    required: list[RequiredSlotType] = []

    def require(slot_type: RequiredSlotType) -> None:
        if slot_type not in required:
            required.append(slot_type)

    if intent.metric_mentions and intent.intent_type != "detail_query":
        require("metric")
    if any(slot.role == "group_by" for slot in dimension_slots):
        require("dimension")
    if any(slot.role == "filter" for slot in dimension_slots):
        require("filter")
    if time_range.value_status == "provided":
        require("time_dimension")

    query_shape = intent.query_shape
    has_multi_value_comparison = (
        intent.intent_type in {"comparison_analysis", "share_analysis"}
        and any(
            slot.role == "filter"
            and isinstance(slot.value, list)
            and len(slot.value) >= 2
            for slot in dimension_slots
        )
    )
    has_multi_period_comparison = (
        intent.intent_type == "comparison_analysis" and len(time_ranges) >= 2
    )
    if has_multi_value_comparison or (
        has_multi_period_comparison and query_shape.time_grain is not None
    ):
        query_shape = query_shape.model_copy(update={"needs_group_by": True})
    if query_shape.needs_order_by:
        require("order")
    if query_shape.limit is not None:
        require("limit")

    if intent.intent_type == "trend_analysis":
        require("metric")
        require("time_dimension")
    elif intent.intent_type == "ranking_analysis":
        require("metric")
        require("dimension")
        require("order")
        require("limit")
    elif intent.intent_type == "comparison_analysis":
        require("metric")
        require("comparison_target")
        # 同一维度的多个值用于对象比较时，该维度既负责筛选范围，也负责结果分组。
        if any(
            slot.role == "filter"
            and isinstance(slot.value, list)
            and len(slot.value) >= 2
            for slot in dimension_slots
        ):
            require("dimension")
    elif intent.intent_type == "share_analysis":
        require("metric")
        # 多个明确类别既限制查询范围，也必须成为占比结果的分组维度。
        if any(
            (
                slot.role == "group_by"
                or (
                    slot.role == "filter"
                    and isinstance(slot.value, list)
                    and len(slot.value) >= 2
                )
            )
            for slot in dimension_slots
        ):
            require("dimension")
    elif intent.intent_type == "detail_query":
        require("dimension")

    return intent.model_copy(
        update={
            "time_range": time_range,
            "time_ranges": time_ranges,
            "time_mentions": _unique_strings(time_mentions),
            "required_slot_types": required,
            "query_shape": query_shape,
            "dimension_slots": dimension_slots,
            "conflict_slots": conflict_slots,
        }
    )


def _apply_temporal_interpretation(
    intent: IntentRecognitionOutput,
    temporal_interpretation: TemporalInterpretationResult,
    *,
    temporal_context: TemporalContext,
) -> IntentRecognitionOutput:
    """将共享时间结果投影为下游兼容意图，模型计划是唯一时间语义来源。"""

    plan = temporal_interpretation.plan
    payload = apply_temporal_interpretation_payload(
        intent.model_dump(mode="json"),
        temporal_interpretation,
    )
    projected = _stabilize_intent(
        IntentRecognitionOutput.model_validate(payload),
        temporal_context,
        use_legacy_time_interpretation=False,
    )
    if plan.grouping is None:
        return projected
    required_slot_types = list(projected.required_slot_types)
    if "time_dimension" not in required_slot_types:
        required_slot_types.append("time_dimension")
    return projected.model_copy(update={"required_slot_types": required_slot_types})


def _temporal_analysis_context(
    intent: IntentRecognitionOutput,
    mention_graph: MentionGraph | None,
) -> dict[str, Any]:
    """把上游已确认的分析关系传给时间模型，避免时间层重新猜测分析语义。"""

    return {
        "intent_type": intent.intent_type,
        "comparison": (
            intent.comparison.model_dump(mode="json")
            if intent.comparison is not None
            else None
        ),
        "query_shape": intent.query_shape.model_dump(mode="json"),
        "time_expressions": (
            [
                {
                    "raw": item.text,
                    "start_offset": item.start_offset,
                    "end_offset": item.end_offset,
                }
                for item in mention_graph.mentions
                if item.kind == "time_expression"
            ]
            if mention_graph is not None
            else []
        ),
        "expressions": (
            [item.model_dump(mode="json") for item in mention_graph.expressions]
            if mention_graph is not None
            else []
        ),
    }


def _is_metric_internal_time_conflict(
    slot: str,
    metric_internal_time_mentions: set[str],
) -> bool:
    """识别由指标内部时间修饰误报出的时间冲突槽位。"""

    normalized = slot.strip().lower()
    if normalized in {"time", "time_range", "时间", "时间范围"}:
        return True
    return any(mention in slot for mention in metric_internal_time_mentions)


def _merge_usage(*items: dict[str, Any]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "total_tokens")
    return {key: sum(int(item.get(key) or 0) for item in items) for key in keys}


def _unique_strings(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


__all__ = [
    "DIMENSION_SYSTEM_PROMPT",
    "INTENT_SYSTEM_PROMPT",
    "QUESTION_UNDERSTANDING_SYSTEM_PROMPT",
    "QuestionUnderstandingError",
    "QuestionUnderstandingModelClient",
    "QuestionUnderstandingModelResponse",
    "QuestionUnderstandingService",
    "REWRITE_SYSTEM_PROMPT",
    "apply_question_understanding_clarification",
]
