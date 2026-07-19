from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Protocol

from apps.chatbi.models import (
    QuestionIntentProjectionData,
    QuestionModelInvocationData,
    QuestionModelJSONMode,
    QuestionModelResponse,
)
from apps.chatbi.services import (
    QuestionIntentProjectionService,
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelService,
)
from apps.chatbi.services.question_understanding_prompt import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.workflow.capabilities.adapters.intent_validation import (
    IntentPostProcessor,
)
from apps.workflow.capabilities.adapters.time_slots import (
    normalize_time_range_payload,
)
from apps.workflow.capabilities.context import ChatBIRunContext
from apps.workflow.schemas.v1 import (
    IntentRecognitionOutput,
    QuestionClassificationOutput,
    QuestionRewriteOutput,
)
from infrastructure.question_model import build_question_model_service


@dataclass(frozen=True)
class QuestionClassificationPrompt:
    """问题分类模型提示词。"""

    system_prompt: str
    user_prompt: str


class QuestionClassificationModelClient(Protocol):
    """问题分类模型客户端协议，便于测试中替换真实大模型。"""

    def __call__(self, prompt: QuestionClassificationPrompt) -> str: ...


class CallableQuestionModelClient:
    """把 Graph 现有可调用模型端口适配到 ChatBI 统一端口。"""

    def __init__(self, client: QuestionClassificationModelClient) -> None:
        self._client = client

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        content = self._client(
            QuestionClassificationPrompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        )
        return QuestionModelResponse(content=content)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntentSubtaskConfig:
    """意图识别子任务并行调度配置。"""

    enabled: bool = True
    max_workers: int = 3
    subtask_timeout_seconds: float = 20.0
    overall_timeout_seconds: float = 25.0


@dataclass(frozen=True)
class IntentSubtaskResult:
    """意图识别子任务执行结果，业务 payload 与执行元信息分离。"""

    name: str
    payload: dict[str, Any]
    status: str
    source: str
    error_code: str | None
    retry_count: int
    duration_ms: int

    def trace_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "source": self.source,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


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
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


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
""".strip().replace("{shared_rewrite_rules}", QUESTION_REWRITE_BUSINESS_RULES)
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
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_intent_recognition_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造意图识别节点的稳定 JSON 提示词。"""

    domain_candidates = _normalize_subject_domain_candidates(subject_domains or [])
    dimension_candidates = _normalize_dimension_candidates(available_dimensions or [])
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
- 普通维度槽位的 value 不得是时间表达；今天/昨天/本月/最近7天/近30天/去年同期/按天/按月 等只能进入 time_mentions、time_range 或 query_shape.time_grain。
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
        "{shared_metric_time_rules}", METRIC_TIME_EXTRACTION_RULES
    ).replace("{shared_dimension_rules}", DIMENSION_EXTRACTION_RULES)
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
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_intent_shape_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造分析形态识别提示词。"""

    domain_candidates = _normalize_subject_domain_candidates(subject_domains or [])
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
    user_prompt = _markdown_user_prompt(
        rewritten_question=rewritten_question,
        subject_domains=domain_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请识别分析类型、查询形态、必需槽位和主题域，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_semantic_mentions_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造指标和时间线索识别提示词。"""

    dimension_candidates = _normalize_dimension_candidates(available_dimensions or [])
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
""".strip().replace("{shared_metric_time_rules}", METRIC_TIME_EXTRACTION_RULES)
    user_prompt = _markdown_user_prompt(
        rewritten_question=rewritten_question,
        available_dimensions=dimension_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请抽取指标线索和时间线索，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def build_dimension_slots_prompt(
    rewritten_question: str,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
    available_dimensions: list[dict[str, Any]] | None = None,
) -> QuestionClassificationPrompt:
    """构造维度槽位识别提示词。"""

    dimension_candidates = _normalize_dimension_candidates(available_dimensions or [])
    plain_dimension_candidates = [candidate for candidate in dimension_candidates if not candidate.get("is_time")]
    time_dimension_candidates = [candidate for candidate in dimension_candidates if candidate.get("is_time")]
    system_prompt = """
# 角色

你是 ChatBI 工作流中的维度槽位识别器，只负责识别维度、维度角色和维度值。

# 输出

只输出 JSON 对象。

```json
{
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "自然语言维度名", "role": "group_by | filter | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
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
""".strip().replace("{shared_dimension_rules}", DIMENSION_EXTRACTION_RULES)
    user_prompt = _markdown_user_prompt(
        rewritten_question=rewritten_question,
        available_dimensions=plain_dimension_candidates,
        time_dimensions=time_dimension_candidates,
        conversation_context=conversation_context or {},
        user_feedback=user_feedback or {},
        task="请识别维度、维度角色和维度值，并严格按指定 JSON 结构输出。",
    )
    return QuestionClassificationPrompt(system_prompt=system_prompt, user_prompt=user_prompt)


def _markdown_user_prompt(
    *,
    rewritten_question: str,
    task: str,
    available_dimensions: list[dict[str, Any]] | None = None,
    time_dimensions: list[dict[str, Any]] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
) -> str:
    parts = ["# 用户问题", rewritten_question]
    if available_dimensions is not None:
        parts.extend(["# 可用维度", _json_block(available_dimensions)])
    if time_dimensions is not None:
        parts.extend(["# 时间字段候选", _json_block(time_dimensions)])
    if subject_domains is not None:
        parts.extend(["# 候选主题域", _json_block(subject_domains)])
    parts.extend(
        [
            "# 会话上下文",
            _json_block(conversation_context or {}),
            "# 用户反馈",
            _json_block(user_feedback or {}),
            "# 任务",
            task,
        ]
    )
    return "\n\n".join(parts).strip()


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _default_subject_domain(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "domain_id": None,
        "domain_name": None,
        "domain_biz_name": None,
        "confidence": 0.0,
        "reason": "",
        "candidate_domain_ids": [],
    }


def _subject_domain_payload(
    candidate: dict[str, Any],
    status: str,
    confidence: float,
    reason: str,
    candidate_domain_ids: list[int] | None = None,
) -> dict[str, Any]:
    domain_id = candidate["domain_id"]
    return {
        "status": status,
        "domain_id": domain_id,
        "domain_name": candidate["name"],
        "domain_biz_name": candidate["biz_name"],
        "confidence": confidence,
        "reason": reason,
        "candidate_domain_ids": candidate_domain_ids or [domain_id],
    }


def _valid_candidate_domain_ids(value: Any, candidate_by_id: dict[int, dict[str, Any]]) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        domain_id = _int_or_none(item)
        if domain_id is not None and domain_id in candidate_by_id and domain_id not in result:
            result.append(domain_id)
    return result


def _normalize_subject_domain_candidates(subject_domains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in subject_domains:
        if not isinstance(item, dict):
            continue
        domain_id = _int_or_none(item.get("domain_id") or item.get("id"))
        if domain_id is None or domain_id in seen:
            continue
        seen.add(domain_id)
        name = str(item.get("name") or item.get("domain_name") or domain_id)
        biz_name = str(item.get("biz_name") or item.get("domain_biz_name") or domain_id)
        candidates.append(
            {
                "domain_id": domain_id,
                "name": name,
                "biz_name": biz_name,
                "description": item.get("description"),
                "model_ids": [_id for _id in (_int_or_none(value) for value in item.get("model_ids") or []) if _id is not None],
            }
        )
    return candidates


def _normalize_dimension_candidates(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in dimensions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("dimension") or "").strip()
        if not name:
            continue
        key = _dimension_text_key(name)
        if key in seen:
            continue
        seen.add(key)
        aliases = _unique_texts(item.get("aliases") or item.get("alias") or [])
        data_type = str(item.get("data_type") or item.get("dataType") or "").strip()
        semantic_type = str(item.get("semantic_type") or item.get("semanticType") or "").strip()
        is_time = bool(item.get("is_time"))
        candidates.append(
            {
                "name": name,
                "aliases": aliases,
                "data_type": data_type or None,
                "semantic_type": semantic_type or None,
                "value_kind": item.get("value_kind") or _infer_dimension_value_kind(name, data_type, semantic_type, is_time),
                "is_time": is_time,
            }
        )
    return candidates


def _dimension_candidate_from_schema_element(dimension: Any) -> dict[str, Any] | None:
    name = str(getattr(dimension, "name", "") or "").strip()
    if not name:
        return None
    aliases = _unique_texts([*(getattr(dimension, "alias", []) or []), *_dimension_name_variants(name)])
    ext_info = getattr(dimension, "ext_info", {}) or {}
    type_params = getattr(dimension, "type_params", {}) or {}
    data_type = str(
        getattr(dimension, "data_type", None)
        or ext_info.get("dimension_data_type")
        or ext_info.get("data_type")
        or ext_info.get("dataType")
        or ""
    ).strip()
    semantic_type = str(ext_info.get("semantic_type") or ext_info.get("semanticType") or "").strip()
    is_time = bool(ext_info.get("is_default_time")) or str(ext_info.get("dimension_type") or "").lower().endswith("time")
    is_time = is_time or bool(type_params.get("timeGranularity"))
    return {
        "name": name,
        "aliases": aliases,
        "data_type": data_type or None,
        "semantic_type": semantic_type or ("time" if is_time else None),
        "value_kind": _infer_dimension_value_kind(name, data_type, semantic_type, is_time),
        "is_time": is_time,
    }


def _infer_dimension_value_kind(name: str, data_type: str | None, semantic_type: str | None, is_time: bool) -> str:
    if is_time:
        return "date"
    normalized_semantic = str(semantic_type or "").lower()
    if normalized_semantic in {"identifier", "id"}:
        return "numeric_id"
    if normalized_semantic in {"name", "label"}:
        return "string_label"
    normalized_type = str(data_type or "").lower()
    if any(token in normalized_type for token in ("int", "number", "numeric", "decimal", "bigint", "smallint")):
        return "numeric_id"
    if name.endswith(("ID", "id", "编号")):
        return "numeric_id"
    return "string_label"


def _dimension_name_variants(name: str) -> list[str]:
    text = name.strip()
    variants: list[str] = []
    for suffix in ("ID", "id", "编号", "名称", "维度"):
        if text.endswith(suffix) and len(text) > len(suffix):
            variants.append(text[: -len(suffix)].strip())
    return variants


def _unique_texts(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    texts: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        key = _dimension_text_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        texts.append(text)
    return texts


def _dimension_text_key(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _dimension_candidate_by_text(
    dimensions: list[dict[str, Any]], *, include_time: bool = True
) -> dict[str, dict[str, Any]]:
    candidates = _normalize_dimension_candidates(dimensions)
    by_text: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not include_time and candidate.get("is_time"):
            continue
        for text in [candidate.get("name"), *(candidate.get("aliases") or [])]:
            key = _dimension_text_key(text)
            if key:
                by_text[key] = candidate
    return by_text


class QuestionAdapter:
    """ChatBI v1 问题节点真实能力适配器。"""

    def __init__(
        self,
        model_client: QuestionClassificationModelClient | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
        intent_post_processor: IntentPostProcessor | None = None,
        intent_subtask_config: IntentSubtaskConfig | None = None,
        question_model_service: QuestionModelService | None = None,
        intent_projection_service: QuestionIntentProjectionService | None = None,
    ) -> None:
        if model_client is not None and question_model_service is not None:
            raise ValueError("QUESTION_MODEL_SOURCE_CONFLICT")
        self._model_client = model_client
        self._question_model_service = (
            QuestionModelService(CallableQuestionModelClient(model_client))
            if model_client is not None
            else question_model_service or build_question_model_service()
        )
        self._schema_provider = schema_provider
        self._intent_post_processor = intent_post_processor or IntentPostProcessor()
        self._intent_projection_service = (
            intent_projection_service or QuestionIntentProjectionService()
        )
        self._intent_subtask_config = intent_subtask_config or IntentSubtaskConfig()
        self._last_intent_subtask_trace: dict[str, Any] = {
            "enabled": False,
            "all_subtasks_fallback": False,
            "subtasks": {},
        }

    @property
    def last_intent_subtask_trace(self) -> dict[str, Any]:
        """返回最近一次意图识别子任务执行摘要，避免调用方修改内部状态。"""

        return {
            "enabled": bool(self._last_intent_subtask_trace.get("enabled")),
            "all_subtasks_fallback": bool(self._last_intent_subtask_trace.get("all_subtasks_fallback")),
            "subtasks": {
                name: dict(value)
                for name, value in dict(self._last_intent_subtask_trace.get("subtasks") or {}).items()
                if isinstance(value, dict)
            },
        }

    def classify(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型完成问题分类，并把输出收敛为稳定 schema。"""

        ctx = ChatBIRunContext(request)
        question = ctx.raw_question

        if not question:
            return self._dump("forbidden", "empty_question", "medium", 1.0)
        if ctx.dataset_id is None:
            return self._dump("forbidden", "missing_dataset", "medium", 1.0)

        prompt = build_question_classification_prompt(
            question=question,
            dataset_id=ctx.dataset_id,
            conversation_context=ctx.conversation,
        )
        try:
            payload = self._invoke_prompt(prompt, "classification")
        except QuestionModelCallError as exc:
            raise RuntimeError("CLASSIFICATION_MODEL_CALL_FAILED") from exc
        except QuestionModelError as exc:
            raise ValueError("CLASSIFICATION_MODEL_OUTPUT_INVALID") from exc
        try:
            output = QuestionClassificationOutput.model_validate(payload)
        except Exception as exc:
            raise ValueError("CLASSIFICATION_MODEL_OUTPUT_INVALID") from exc
        return output.model_dump(mode="json")

    def rewrite(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型补全上下文，输出稳定的问题重写结果。"""

        ctx = ChatBIRunContext(request)
        question = ctx.raw_question
        user_feedback = ctx.rewrite_response
        if not question:
            return self._rewrite_dump("", True, ["question"], None)

        prompt = build_question_rewrite_prompt(
            question=question,
            dataset_id=ctx.dataset_id,
            conversation_context=ctx.conversation,
            user_feedback=user_feedback,
        )
        try:
            payload = self._invoke_prompt(prompt, "rewrite")
            output = QuestionRewriteOutput.model_validate(payload)
        except Exception:
            return self._rewrite_fallback(question, user_feedback)
        output = self._normalize_rewrite_output(output, dataset_id=ctx.dataset_id)
        return output.model_dump(mode="json")

    def recognize_intent(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型分段识别分析意图，并在失败时使用轻量规则兜底。"""

        ctx = ChatBIRunContext(request)
        rewritten_question = ctx.question
        user_feedback = ctx.intent_response
        schema = self._load_dataset_schema(ctx)
        subject_domains = self._subject_domains_from_schema(schema)
        available_dimensions = self._available_dimensions_from_schema(schema)
        if not rewritten_question:
            return self._intent_dump("unknown", 0.4, ["metric"], [])

        conversation_context = ctx.conversation
        fallback = self._intent_fallback(rewritten_question)
        fallback_payloads = self._intent_subtask_fallback_payloads(fallback)
        subtask_results = self._run_intent_subtasks(
            {
                "shape": lambda: self._recognize_intent_shape(
                    rewritten_question,
                    conversation_context,
                    user_feedback,
                    subject_domains,
                    fallback,
                ),
                "semantic": lambda: self._recognize_semantic_mentions(
                    rewritten_question,
                    conversation_context,
                    user_feedback,
                    available_dimensions,
                    fallback,
                ),
                "dimensions": lambda: self._recognize_dimension_slots(
                    rewritten_question,
                    conversation_context,
                    user_feedback,
                    available_dimensions,
                    fallback,
                ),
            },
            fallback_payloads,
        )
        shape = subtask_results["shape"].payload
        semantic = subtask_results["semantic"].payload
        dimensions = subtask_results["dimensions"].payload
        projection = self._intent_projection_service.project(
            QuestionIntentProjectionData(
                shape=shape,
                semantic=semantic,
                dimensions=dimensions,
                user_feedback=user_feedback,
            )
        )
        output = IntentRecognitionOutput.model_validate(projection.payload)
        validation = self._intent_post_processor.validate(output.model_dump(mode="json"), retry_count=0)
        return output.model_copy(update={"validation": validation}).model_dump(mode="json")

    def _intent_subtask_fallback_payloads(self, fallback: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "shape": {
                "intent_type": fallback.get("intent_type"),
                "confidence": fallback.get("confidence", 0),
                "required_slot_types": fallback.get("required_slot_types", []),
                "query_shape": fallback.get("query_shape", {}),
                "subject_domain": fallback.get("subject_domain") or _default_subject_domain("not_required"),
                "ambiguous_slots": fallback.get("ambiguous_slots", []),
                "conflict_slots": fallback.get("conflict_slots", []),
            },
            "semantic": {
                "metric_mentions": fallback.get("metric_mentions", []),
                "time_mentions": fallback.get("time_mentions", []),
                "time_range": normalize_time_range_payload(
                    fallback.get("time_range") or {"raw": None, "value_status": "not_provided"}
                ),
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
            "dimensions": {
                "dimension_mentions": fallback.get("dimension_mentions", []),
                "dimension_slots": fallback.get("dimension_slots", []),
                "residual_filter_mentions": fallback.get("filter_mentions", []),
                "ambiguous_slots": [],
                "conflict_slots": [],
            },
        }

    def _run_intent_subtasks(
        self,
        tasks: dict[str, Callable[[], dict[str, Any]]],
        fallback_payloads: dict[str, dict[str, Any]],
    ) -> dict[str, IntentSubtaskResult]:
        if not self._intent_subtask_config.enabled:
            results = {
                name: self._run_intent_subtask(name, task, fallback_payloads[name])
                for name, task in tasks.items()
            }
            self._record_intent_subtask_trace(enabled=False, results=results)
            return results

        max_workers = max(1, min(self._intent_subtask_config.max_workers, len(tasks)))
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="intent-subtask")
        try:
            future_to_name: dict[Future[IntentSubtaskResult], str] = {
                executor.submit(self._run_intent_subtask, name, task, fallback_payloads[name]): name
                for name, task in tasks.items()
            }
            timeout_seconds = min(
                self._intent_subtask_config.subtask_timeout_seconds,
                self._intent_subtask_config.overall_timeout_seconds,
            )
            done, not_done = wait(future_to_name, timeout=timeout_seconds)
            results: dict[str, IntentSubtaskResult] = {}
            for future in done:
                name = future_to_name[future]
                try:
                    results[name] = future.result(timeout=0)
                except Exception as exc:
                    results[name] = self._fallback_intent_subtask_result(
                        name=name,
                        fallback_payload=fallback_payloads[name],
                        source="exception_fallback",
                        error_code=exc.__class__.__name__,
                        started_at=None,
                    )
            for future in not_done:
                name = future_to_name[future]
                future.cancel()
                results[name] = self._fallback_intent_subtask_result(
                    name=name,
                    fallback_payload=fallback_payloads[name],
                    source="timeout_fallback",
                    error_code="INTENT_SUBTASK_TIMEOUT",
                    started_at=None,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        ordered_results = {name: results[name] for name in tasks}
        self._record_intent_subtask_trace(enabled=True, results=ordered_results)
        return ordered_results

    def _run_intent_subtask(
        self,
        name: str,
        task: Callable[[], dict[str, Any]],
        fallback_payload: dict[str, Any],
    ) -> IntentSubtaskResult:
        start = time.monotonic()
        try:
            payload = task()
            status = "succeeded"
            source = "model"
            error_code = None
        except Exception as exc:
            payload = dict(fallback_payload)
            status = "fallback"
            source = "exception_fallback"
            error_code = exc.__class__.__name__
            logger.warning("intent subtask failed; using fallback", extra={"subtask": name, "error_code": error_code})
        return IntentSubtaskResult(
            name=name,
            payload=payload,
            status=status,
            source=source,
            error_code=error_code,
            retry_count=0,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def _fallback_intent_subtask_result(
        self,
        name: str,
        fallback_payload: dict[str, Any],
        source: str,
        error_code: str,
        started_at: float | None,
    ) -> IntentSubtaskResult:
        duration_ms = 0 if started_at is None else int((time.monotonic() - started_at) * 1000)
        return IntentSubtaskResult(
            name=name,
            payload=dict(fallback_payload),
            status="fallback",
            source=source,
            error_code=error_code,
            retry_count=0,
            duration_ms=duration_ms,
        )

    def _record_intent_subtask_trace(
        self,
        enabled: bool,
        results: dict[str, IntentSubtaskResult],
    ) -> None:
        self._last_intent_subtask_trace = {
            "enabled": enabled,
            "all_subtasks_fallback": all(result.status == "fallback" for result in results.values()),
            "subtasks": {name: result.trace_payload() for name, result in results.items()},
        }

    def _recognize_intent_shape(
        self,
        rewritten_question: str,
        conversation_context: dict[str, Any],
        user_feedback: dict[str, Any],
        subject_domains: list[dict[str, Any]],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        def prompt(feedback: dict[str, Any]) -> QuestionClassificationPrompt:
            return build_intent_shape_prompt(
                rewritten_question=rewritten_question,
                conversation_context=conversation_context,
                user_feedback=feedback,
                subject_domains=subject_domains,
            )

        fallback_payload = self._intent_subtask_fallback_payloads(fallback)["shape"]
        return self._recognize_subtask(
            "intent_shape",
            prompt,
            {**user_feedback},
            fallback_payload,
            lambda payload: self._validate_intent_shape_payload(payload, subject_domains),
        )

    def _recognize_semantic_mentions(
        self,
        rewritten_question: str,
        conversation_context: dict[str, Any],
        user_feedback: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        def prompt(feedback: dict[str, Any]) -> QuestionClassificationPrompt:
            return build_semantic_mentions_prompt(
                rewritten_question=rewritten_question,
                conversation_context=conversation_context,
                user_feedback=feedback,
                available_dimensions=available_dimensions,
            )

        fallback_payload = self._intent_subtask_fallback_payloads(fallback)["semantic"]
        return self._recognize_subtask(
            "semantic_mentions",
            prompt,
            {**user_feedback},
            fallback_payload,
            self._validate_semantic_mentions_payload,
        )

    def _recognize_dimension_slots(
        self,
        rewritten_question: str,
        conversation_context: dict[str, Any],
        user_feedback: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        def prompt(feedback: dict[str, Any]) -> QuestionClassificationPrompt:
            return build_dimension_slots_prompt(
                rewritten_question=rewritten_question,
                conversation_context=conversation_context,
                user_feedback=feedback,
                available_dimensions=available_dimensions,
            )

        fallback_payload = self._intent_subtask_fallback_payloads(fallback)["dimensions"]
        return self._recognize_subtask(
            "dimension_slots",
            prompt,
            {**user_feedback},
            fallback_payload,
            lambda payload: self._validate_dimension_slots_payload(payload, available_dimensions),
        )

    def _recognize_subtask(
        self,
        stage: str,
        prompt_builder,
        user_feedback: dict[str, Any],
        fallback_payload: dict[str, Any],
        validator,
    ) -> dict[str, Any]:
        result: dict[str, Any] = fallback_payload
        retry_feedback: dict[str, Any] = {}
        for retry_count in range(self._intent_post_processor.max_retry_count):
            prompt = prompt_builder({**user_feedback, **retry_feedback})
            result = self._invoke_prompt(prompt, stage)
            validation = validator(result)
            result = validation["payload"]
            if validation["status"] == "invalid" and validation["retryable"] and retry_count + 1 < self._intent_post_processor.max_retry_count:
                retry_feedback = {
                    "reason_code": validation["reason_code"],
                    "feedback": validation["repair_hint"],
                }
                continue
            return result
        return result

    def _invoke_prompt(
        self,
        prompt: QuestionClassificationPrompt,
        stage: str,
    ) -> dict[str, Any]:
        return self._question_model_service.invoke(
            QuestionModelInvocationData(
                stage=stage,
                system_prompt=prompt.system_prompt,
                user_prompt=prompt.user_prompt,
                json_mode=QuestionModelJSONMode.EXTRACT_OBJECT,
            )
        ).payload

    def _validate_intent_shape_payload(
        self,
        payload: dict[str, Any],
        subject_domains: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = self._intent_projection_service.normalize_shape(
            payload,
            subject_domain=self._normalize_subject_domain_output(
                payload.get("subject_domain"),
                subject_domains,
            ),
        )
        return {"status": "valid", "retryable": False, "reason_code": "VALID", "repair_hint": None, "payload": normalized}

    def _validate_semantic_mentions_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._intent_projection_service.normalize_semantic(payload)
        return {"status": "valid", "retryable": False, "reason_code": "VALID", "repair_hint": None, "payload": normalized}

    def _validate_dimension_slots_payload(
        self,
        payload: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = self._normalize_dimension_slots_payload(payload, available_dimensions)
        shared_validation = self._intent_post_processor.validate(normalized)
        if shared_validation["status"] == "invalid":
            return {
                "status": "invalid",
                "retryable": bool(shared_validation["retryable"]),
                "reason_code": shared_validation["reason_code"],
                "repair_hint": shared_validation["repair_hint"],
                "payload": normalized,
            }
        violation = self._first_dimension_slot_violation(normalized, available_dimensions)
        if violation is None:
            return {"status": "valid", "retryable": False, "reason_code": "VALID", "repair_hint": None, "payload": normalized}
        return {
            "status": "invalid",
            "retryable": True,
            "reason_code": violation["reason_code"],
            "repair_hint": violation["repair_hint"],
            "payload": normalized,
        }

    def _normalize_dimension_slots_payload(
        self,
        payload: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized_candidates = _normalize_dimension_candidates(available_dimensions)
        candidate_by_text = _dimension_candidate_by_text(normalized_candidates, include_time=False)
        candidate_by_text_with_time = _dimension_candidate_by_text(normalized_candidates, include_time=True)
        if not normalized_candidates:
            slots = [dict(slot) for slot in payload.get("dimension_slots") or [] if isinstance(slot, dict)]
            mentions = self._intent_projection_service.unique_strings(
                [
                    *self._intent_projection_service.normalize_text_list(
                        payload.get("dimension_mentions")
                    ),
                    *[str(slot.get("name")) for slot in slots if slot.get("name")],
                ]
            )
            return {
                "dimension_mentions": mentions,
                "dimension_slots": slots,
                "residual_filter_mentions": [
                    item for item in payload.get("residual_filter_mentions") or [] if isinstance(item, dict)
                ],
                "ambiguous_slots": self._intent_projection_service.normalize_text_list(
                    payload.get("ambiguous_slots")
                ),
                "conflict_slots": self._intent_projection_service.normalize_text_list(
                    payload.get("conflict_slots")
                ),
            }
        mentions: list[str] = []
        for mention in self._intent_projection_service.normalize_text_list(
            payload.get("dimension_mentions")
        ):
            mention_key = _dimension_text_key(mention)
            if mention_key in candidate_by_text_with_time and mention_key not in candidate_by_text:
                continue
            candidate = candidate_by_text.get(mention_key)
            normalized_mention = candidate["name"] if candidate is not None else mention
            if normalized_mention not in mentions:
                mentions.append(normalized_mention)

        slots: list[dict[str, Any]] = []
        for slot in payload.get("dimension_slots") or []:
            if not isinstance(slot, dict):
                continue
            raw_name = str(slot.get("name") or slot.get("dimension") or "").strip()
            if not raw_name:
                continue
            raw_name_key = _dimension_text_key(raw_name)
            if raw_name_key in candidate_by_text_with_time and raw_name_key not in candidate_by_text:
                continue
            candidate = candidate_by_text.get(raw_name_key)
            slot_name = candidate["name"] if candidate is not None else raw_name
            if candidate is None:
                normalized_slot = {
                    "name": slot_name,
                    "role": self._intent_projection_service.normalize_dimension_role(
                        slot.get("role")
                    ),
                    "value": slot.get("value"),
                    "value_status": self._intent_projection_service.normalize_value_status(
                        slot.get("value_status"), slot.get("value")
                    ),
                }
                if "value_confidence" in slot:
                    normalized_slot["value_confidence"] = (
                        self._intent_projection_service.normalize_confidence(
                            slot.get("value_confidence")
                        )
                    )
                slots.append(normalized_slot)
                if slot_name not in mentions:
                    mentions.append(slot_name)
                continue
            normalized_slot = {
                "name": slot_name,
                "role": self._intent_projection_service.normalize_dimension_role(
                    slot.get("role")
                ),
                "value": slot.get("value"),
                "value_status": self._intent_projection_service.normalize_value_status(
                    slot.get("value_status"), slot.get("value")
                ),
            }
            if "value_confidence" in slot:
                normalized_slot["value_confidence"] = (
                    self._intent_projection_service.normalize_confidence(
                        slot.get("value_confidence")
                    )
                )
            slots.append(normalized_slot)
            if slot_name not in mentions:
                mentions.append(slot_name)

        return {
            "dimension_mentions": mentions,
            "dimension_slots": slots,
            "residual_filter_mentions": [
                item for item in payload.get("residual_filter_mentions") or [] if isinstance(item, dict)
            ],
            "ambiguous_slots": self._intent_projection_service.normalize_text_list(
                payload.get("ambiguous_slots")
            ),
            "conflict_slots": self._intent_projection_service.normalize_text_list(
                payload.get("conflict_slots")
            ),
        }

    @classmethod
    def _first_dimension_slot_violation(
        cls,
        payload: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        candidate_by_name = {item["name"]: item for item in _normalize_dimension_candidates(available_dimensions)}
        for slot in payload.get("dimension_slots") or []:
            if not isinstance(slot, dict):
                continue
            value = slot.get("value")
            candidate = candidate_by_name.get(str(slot.get("name") or ""))
            if candidate is None:
                continue
            if str(candidate.get("value_kind") or "") == "string_label":
                continue
            if cls._value_contains_dimension_text(value, candidate):
                return {
                    "reason_code": "DIMENSION_VALUE_CONTAINS_DIMENSION_TEXT",
                    "repair_hint": "维度值不能包含已命中的维度名或别名，请只输出值本身。",
                }
        return None

    @staticmethod
    def _value_contains_dimension_text(value: Any, candidate: dict[str, Any]) -> bool:
        if not isinstance(value, str):
            return False
        value_key = _dimension_text_key(value)
        if not value_key:
            return False
        texts = [candidate.get("name"), *(candidate.get("aliases") or [])]
        return any(_dimension_text_key(text) and _dimension_text_key(text) in value_key for text in texts)

    def _load_dataset_schema(self, ctx: ChatBIRunContext) -> Any | None:
        if self._schema_provider is None:
            return None
        if ctx.dataset_id is None:
            return None
        try:
            return self._schema_provider.build_dataset_schema(ctx.tenant_id, ctx.dataset_id)
        except Exception:
            return None

    @staticmethod
    def _subject_domains_from_schema(schema: Any | None) -> list[dict[str, Any]]:
        if schema is None:
            return []
        return _normalize_subject_domain_candidates(getattr(schema, "subject_domains", []) or [])

    @staticmethod
    def _available_dimensions_from_schema(schema: Any | None) -> list[dict[str, Any]]:
        if schema is None:
            return []
        candidates: list[dict[str, Any]] = []
        for dimension in getattr(schema, "dimensions", []) or []:
            candidate = _dimension_candidate_from_schema_element(dimension)
            if candidate is not None:
                candidates.append(candidate)
        return _normalize_dimension_candidates(candidates)

    @staticmethod
    def _dump(category: str, reason: str, risk_level: str, confidence: float) -> dict[str, Any]:
        return QuestionClassificationOutput(
            category=category,
            reason=reason,
            risk_level=risk_level,
            confidence=confidence,
        ).model_dump(mode="json")

    @staticmethod
    def _rewrite_dump(
        rewritten_question: str,
        need_user_input: bool,
        missing_slots: list[str],
        image_profile_hint: str | None,
    ) -> dict[str, Any]:
        return QuestionRewriteOutput(
            rewritten_question=rewritten_question,
            need_user_input=need_user_input,
            missing_slots=missing_slots,
            image_profile_hint=image_profile_hint,
        ).model_dump(mode="json")

    @staticmethod
    def _normalize_rewrite_output(output: QuestionRewriteOutput, dataset_id: Any) -> QuestionRewriteOutput:
        """上游已提供 dataset_id 时，防御模型误把 dataset_id 当成缺失槽位。"""

        if dataset_id is None:
            return output
        missing_slots = [slot for slot in output.missing_slots if slot != "dataset_id"]
        if missing_slots == output.missing_slots:
            return output
        return output.model_copy(
            update={
                "missing_slots": missing_slots,
                "need_user_input": bool(missing_slots),
            }
        )

    @classmethod
    def _rewrite_fallback(cls, question: str, user_feedback: dict[str, Any]) -> dict[str, Any]:
        """模型不可用时保留最小澄清兜底，避免交互分支失去回归入口。"""

        need_user_input = not user_feedback and any(keyword in question for keyword in ("需要澄清", "信息不足", "补充"))
        return cls._rewrite_dump(
            question,
            need_user_input,
            ["metric"] if need_user_input else [],
            None,
        )

    @staticmethod
    def _intent_dump(
        intent_type: str,
        confidence: float,
        ambiguous_slots: list[str],
        conflict_slots: list[str],
        metric_mentions: list[str] | None = None,
        dimension_mentions: list[str] | None = None,
        time_mentions: list[str] | None = None,
        filter_mentions: list[dict[str, Any]] | None = None,
        dimension_slots: list[dict[str, Any]] | None = None,
        time_range: dict[str, Any] | None = None,
        required_slot_types: list[str] | None = None,
        query_shape: dict[str, Any] | None = None,
        subject_domain: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return IntentRecognitionOutput(
            intent_type=intent_type,
            confidence=confidence,
            metric_mentions=metric_mentions or [],
            dimension_mentions=dimension_mentions or [],
            dimension_slots=dimension_slots or [],
            time_mentions=time_mentions or [],
            time_range=time_range or {"raw": None, "value_status": "not_provided"},
            filter_mentions=filter_mentions or [],
            required_slot_types=required_slot_types or [],
            query_shape=query_shape or {},
            subject_domain=subject_domain or _default_subject_domain("not_required"),
            ambiguous_slots=ambiguous_slots,
            conflict_slots=conflict_slots,
        ).model_dump(mode="json")

    @classmethod
    def _intent_fallback(cls, question: str) -> dict[str, Any]:
        """模型不可用时用轻量规则识别常见分析意图。"""

        if any(word in question for word in ("看一下情况", "分析一下", "怎么样", "看看数据")):
            return cls._intent_dump(
                "unknown",
                0.4,
                ["metric"],
                [],
                required_slot_types=["metric"],
                query_shape={"select_mode": "unknown"},
            )
        metric_mentions = cls._extract_metric_mentions(question)
        dimension_mentions = cls._extract_dimension_mentions(question)
        time_mentions = cls._extract_time_mentions(question)
        dimension_slots = cls._dimension_slots_from_question(question, dimension_mentions)
        time_range = cls._time_range_from_mentions(time_mentions)
        if any(word in question for word in ("趋势", "走势", "变化", "按天", "按周", "按月")):
            return cls._intent_dump(
                "trend_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "time_dimension"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "time_grain": cls._infer_time_grain(question),
                },
            )
        if any(word in question for word in ("最高", "最低", "最好", "最差", "top", "Top", "前", "后", "排名")):
            return cls._intent_dump(
                "ranking_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "dimension", "order", "limit"],
                query_shape={
                    "select_mode": "aggregate",
                    "needs_group_by": True,
                    "needs_order_by": True,
                    "order_direction": cls._infer_order_direction(question),
                    "limit": cls._infer_limit(question),
                },
            )
        if any(word in question for word in ("同比", "环比", "对比", "较上期", "比较")):
            return cls._intent_dump(
                "comparison_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "comparison_target"],
                query_shape={"select_mode": "aggregate", "needs_group_by": bool(dimension_mentions)},
            )
        if any(word in question for word in ("占比", "构成", "比例")):
            return cls._intent_dump(
                "share_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "dimension"],
                query_shape={"select_mode": "share", "needs_group_by": True},
            )
        if any(word in question for word in ("异常", "波动", "下降原因", "上升原因", "为什么下降", "为什么上升")):
            return cls._intent_dump(
                "anomaly_analysis",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["metric", "time_range"],
                query_shape={"select_mode": "diagnostic"},
            )
        if any(word in question for word in ("明细", "详情", "列表", "清单")):
            return cls._intent_dump(
                "detail_query",
                0.85,
                [],
                [],
                metric_mentions=metric_mentions,
                dimension_mentions=dimension_mentions,
                time_mentions=time_mentions,
                dimension_slots=dimension_slots,
                time_range=time_range,
                required_slot_types=["dimension"],
                query_shape={"select_mode": "detail"},
            )
        return cls._intent_dump(
            "metric_query",
            0.85,
            [],
            [],
            metric_mentions=metric_mentions,
            dimension_mentions=dimension_mentions,
            time_mentions=time_mentions,
            dimension_slots=dimension_slots,
            time_range=time_range,
            required_slot_types=["metric"],
            query_shape={"select_mode": "aggregate", "needs_group_by": bool(dimension_mentions)},
        )

    @staticmethod
    def _normalize_subject_domain_output(
        raw_subject_domain: Any,
        subject_domains: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = _normalize_subject_domain_candidates(subject_domains)
        if not candidates:
            return _default_subject_domain("not_required")

        if len(candidates) == 1:
            candidate = candidates[0]
            return _subject_domain_payload(candidate, status="not_required", confidence=1.0, reason="只有一个候选主题域")

        raw = raw_subject_domain if isinstance(raw_subject_domain, dict) else {}
        candidate_by_id = {candidate["domain_id"]: candidate for candidate in candidates}
        selected_domain_id = _int_or_none(raw.get("domain_id") or raw.get("id"))
        if str(raw.get("status") or "").lower() == "selected" and selected_domain_id in candidate_by_id:
            candidate = candidate_by_id[selected_domain_id]
            return _subject_domain_payload(
                candidate,
                status="selected",
                confidence=float(raw.get("confidence") or 0),
                reason=str(raw.get("reason") or ""),
                candidate_domain_ids=_valid_candidate_domain_ids(raw.get("candidate_domain_ids"), candidate_by_id)
                or [selected_domain_id],
            )

        candidate_ids = _valid_candidate_domain_ids(raw.get("candidate_domain_ids"), candidate_by_id)
        if not candidate_ids:
            candidate_ids = [candidate["domain_id"] for candidate in candidates]
        status = "not_matched" if str(raw.get("status") or "").lower() == "not_matched" else "ambiguous"
        return {
            **_default_subject_domain(status),
            "confidence": float(raw.get("confidence") or 0),
            "reason": str(raw.get("reason") or "主题域未能唯一确定"),
            "candidate_domain_ids": candidate_ids,
        }

    @classmethod
    def _dimension_slots_from_question(cls, question: str, dimension_mentions: list[str]) -> list[dict[str, Any]]:
        """抽取维度角色和值状态，值缺失时显式标记而不是伪造业务值。"""

        slots: list[dict[str, Any]] = []
        for dimension in dimension_mentions:
            value = cls._extract_dimension_value(question, dimension)
            if value is not None:
                slots.append(
                    {
                        "name": dimension,
                        "role": "filter",
                        "value": value,
                        "value_status": "provided",
                    }
                )
                continue
            role = "group_by" if cls._is_group_by_dimension(question, dimension) else "ambiguous"
            slots.append(
                {
                    "name": dimension,
                    "role": role,
                    "value": None,
                    "value_status": "not_provided",
                }
            )
        return slots

    @staticmethod
    def _extract_dimension_value(question: str, dimension: str) -> str | None:
        """识别“1号档口/档口 1”这类常见维度值表达。"""

        import re

        patterns = (
            rf"([A-Za-z0-9一二三四五六七八九十百千万]+)\s*号?\s*{re.escape(dimension)}",
            rf"{re.escape(dimension)}\s*([A-Za-z0-9一二三四五六七八九十百千万]+)\s*号?",
        )
        for pattern in patterns:
            matched = re.search(pattern, question)
            if matched:
                return matched.group(1)
        return None

    @staticmethod
    def _is_group_by_dimension(question: str, dimension: str) -> bool:
        """判断用户是否明确要求按某个维度分组。"""

        group_markers = (
            f"各{dimension}",
            f"每个{dimension}",
            f"按{dimension}",
            f"分{dimension}",
            f"{dimension}排行",
            f"{dimension}排名",
        )
        ranking_markers = ("最高", "最低", "最好", "最差", "top", "Top", "前", "后", "排名")
        return any(marker in question for marker in group_markers) or (
            dimension in question and any(marker in question for marker in ranking_markers)
        )

    @staticmethod
    def _time_range_from_mentions(time_mentions: list[str]) -> dict[str, Any]:
        if not time_mentions:
            return {"raw": None, "value_status": "not_provided"}
        return {"raw": time_mentions[0], "value_status": "provided"}

    @staticmethod
    def _extract_metric_mentions(question: str) -> list[str]:
        """从问题中提取常见自然语言指标线索，不做资产确认。"""

        keywords = (
            "销售额",
            "订单数",
            "访问人数",
            "访问量",
            "用户数",
            "利润",
            "GMV",
            "成交额",
            "收入",
            "客单价",
        )
        return [keyword for keyword in keywords if keyword in question]

    @staticmethod
    def _extract_dimension_mentions(question: str) -> list[str]:
        """从问题中提取常见自然语言维度线索，不做资产确认。"""

        keywords = ("商品", "地区", "区域", "渠道", "店铺", "客户", "用户", "日期", "月份", "城市", "档口")
        mentions = [keyword for keyword in keywords if keyword in question]
        if any(word in question for word in ("按天", "按周", "按月")) and "日期" not in mentions and "月份" not in mentions:
            mentions.append("日期")
        return mentions

    @staticmethod
    def _extract_time_mentions(question: str) -> list[str]:
        """从问题中提取自然语言时间线索。"""

        keywords = ("今天", "昨日", "昨天", "本周", "上周", "本月", "上月", "最近 7 天", "最近7天", "近 30 天", "近30天")
        mentions = [keyword for keyword in keywords if keyword in question]
        absolute_months = re.findall(r"\d{4}\s*年\s*\d{1,2}\s*月", question)
        mentions.extend(month for month in absolute_months if month not in mentions)
        for keyword in ("按天", "按周", "按月"):
            if keyword in question:
                mentions.append(keyword)
        return mentions

    @staticmethod
    def _infer_time_grain(question: str) -> str | None:
        if "按月" in question:
            return "month"
        if "按周" in question:
            return "week"
        if "按天" in question or "趋势" in question or "走势" in question:
            return "day"
        return None

    @staticmethod
    def _infer_order_direction(question: str) -> str:
        if any(word in question for word in ("最低", "最差", "后")):
            return "asc"
        return "desc"

    @staticmethod
    def _infer_limit(question: str) -> int | None:
        natural_limit = re.search(r"(?:最高|最低|最好|最差)(?:的)?\s*(\d+)\s*个", question)
        if natural_limit:
            return int(natural_limit.group(1))
        for marker in ("Top", "top", "前", "后"):
            index = question.find(marker)
            if index < 0:
                continue
            digits = "".join(char for char in question[index + len(marker) : index + len(marker) + 3] if char.isdigit())
            if digits:
                return int(digits)
        return None
