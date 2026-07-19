"""ChatBI 共享问题理解能力：上下文重写、意图识别与确定性校验。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

import orjson
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.chatbi.models.dto.question_understanding import (
    QuestionUnderstandingValidationData,
)
from apps.chatbi.services.question_understanding_validation_service import (
    QuestionUnderstandingValidationService,
)
from apps.chatbi.services.time_range import normalize_time_range_payload


class QuestionUnderstandingError(RuntimeError):
    """问题理解阶段失败，调用方应明确终止当前问数流程。"""


class QuestionRewriteOutput(BaseModel):
    """只表达上下文补全结果，不承载业务意图和工具决策。"""

    model_config = ConfigDict(extra="forbid")

    message_type: Literal["new_question", "followup", "clarification_reply"]
    rewritten_question: str = Field(min_length=1)
    inherited_context: dict[str, Any] = Field(default_factory=dict)
    need_user_input: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class DimensionSlot(BaseModel):
    """自然语言维度槽位，不绑定字段或语义资产。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: Literal["group_by", "filter", "ambiguous"]
    value: str | int | float | bool | None = None
    value_status: Literal["provided", "not_provided", "ambiguous"] = "not_provided"
    value_confidence: float = Field(default=0.0, ge=0, le=1)


class TimeRange(BaseModel):
    """保留原始时间表达，并承载系统确定性生成的时间 AST。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = None
    value_status: Literal["provided", "not_provided"] = "not_provided"
    normalized: dict[str, Any] | None = None


class IntentRecognitionOutput(BaseModel):
    """自然语言层意图，不包含工具、字段、表或资产 ID。"""

    model_config = ConfigDict(extra="forbid")

    intent_type: Literal[
        "metric_query",
        "trend_analysis",
        "ranking_analysis",
        "comparison_analysis",
        "detail_query",
        "share_analysis",
        "anomaly_analysis",
        "unknown",
    ]
    confidence: float = Field(ge=0, le=1)
    metric_mentions: list[str] = Field(default_factory=list)
    dimension_mentions: list[str] = Field(default_factory=list)
    dimension_slots: list[DimensionSlot] = Field(default_factory=list)
    time_mentions: list[str] = Field(default_factory=list)
    time_range: TimeRange = Field(default_factory=TimeRange)
    filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    required_slot_types: list[
        Literal[
            "metric",
            "dimension",
            "time_dimension",
            "time_range",
            "filter",
            "order",
            "limit",
            "comparison_target",
        ]
    ] = Field(default_factory=list)
    query_shape: dict[str, Any] = Field(default_factory=dict)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)


class DimensionRecognitionOutput(BaseModel):
    """独立维度子任务输出，避免综合意图识别遗漏业务对象。"""

    model_config = ConfigDict(extra="forbid")

    dimension_mentions: list[str] = Field(default_factory=list)
    dimension_slots: list[DimensionSlot] = Field(default_factory=list)
    filter_mentions: list[dict[str, Any]] = Field(default_factory=list)
    ambiguous_slots: list[str] = Field(default_factory=list)
    conflict_slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dimension_coverage(self) -> DimensionRecognitionOutput:
        """维度 mention 与结构化槽位必须一一对应，禁止合法 JSON 掩盖漏槽。"""

        mentions = _unique_strings(self.dimension_mentions)
        slot_names = [slot.name for slot in self.dimension_slots]
        if len(slot_names) != len(set(slot_names)) or set(mentions) != set(slot_names):
            raise ValueError("dimension_mentions 与 dimension_slots 必须一一对应")
        self.dimension_mentions = mentions
        return self


class IntentValidationOutput(BaseModel):
    """确定性校验结果，供 Agent 决定是否必须先澄清。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "clarification_required"]
    reason_codes: list[str] = Field(default_factory=list)
    clarification_slots: list[str] = Field(default_factory=list)


class QuestionUnderstandingOutput(BaseModel):
    """问题理解统一输出，是后续 Agent 与工具共享的唯一事实源。"""

    model_config = ConfigDict(extra="forbid")

    original_question: str
    message_type: Literal["new_question", "followup", "clarification_reply"]
    rewritten_question: str
    inherited_context: dict[str, Any] = Field(default_factory=dict)
    intent: IntentRecognitionOutput
    validation: IntentValidationOutput


@dataclass(frozen=True)
class QuestionUnderstandingModelResponse:
    """单次问题理解模型响应。"""

    content: str
    usage_metadata: dict[str, Any]


@dataclass(frozen=True)
class QuestionUnderstandingOutcome:
    """统一业务输出与两次模型调用的累计用量。"""

    output: QuestionUnderstandingOutput
    usage_metadata: dict[str, int]


class QuestionUnderstandingModelClient(Protocol):
    """问题理解模型协议，测试可注入确定性实现。"""

    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionUnderstandingModelResponse: ...


class DefaultQuestionUnderstandingModelClient:
    """默认问题理解模型客户端，按需加载系统默认模型。"""

    def __init__(self) -> None:
        self._llm: BaseChatModel | None = None

    def invoke(self, system_prompt: str, user_prompt: str) -> QuestionUnderstandingModelResponse:
        response = self._get_llm().invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        content = _message_content_text(response)
        if not content:
            raise QuestionUnderstandingError("QUESTION_UNDERSTANDING_MODEL_EMPTY_RESPONSE")
        return QuestionUnderstandingModelResponse(
            content=content,
            usage_metadata=dict(getattr(response, "usage_metadata", None) or {}),
        )

    def _get_llm(self) -> BaseChatModel:
        if self._llm is None:
            from apps.ai_model.model_factory import LLMFactory, get_default_config

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                config = asyncio.run(get_default_config())
            else:
                raise RuntimeError("question understanding model cannot be loaded inside a running event loop")
            self._llm = LLMFactory.create_llm(config).llm
        return self._llm


REWRITE_SYSTEM_PROMPT = """
你是 ChatBI 的问题上下文化重写器。你只负责判断当前输入与会话上下文的关系，并将其重写为语义完整、可独立理解的问题。

禁止事项：
- 不回答问题，不生成 SQL，不选择或提及工具。
- 不识别资产 ID、字段名、表名或指标口径。
- 不添加用户和上下文都没有表达的指标、维度、时间、筛选、比较、排序或默认值。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "message_type": "new_question | followup | clarification_reply",
  "rewritten_question": "补全上下文后的完整问题",
  "inherited_context": {},
  "need_user_input": false,
  "missing_slots": [],
  "confidence": 0.0
}

规则：
- 当前输入本身构成完整问题时，message_type=new_question，不得被历史问题覆盖。
- conversation_context.last_rewritten_question 只表示最近一次成功执行后的完整问题，只能用于补全真正依赖上文的追问。
- “那上个月呢”“换成订单数”等依赖上文的表达属于 followup；只从 last_rewritten_question 继承本轮缺失的语义，并保留本轮明确表达的全部内容。
- 当前输入可以独立理解时，必须忽略 last_rewritten_question，rewritten_question 必须保持当前输入原文。
- 当前输入明显在回答挂起澄清时，message_type=clarification_reply，并将回答合并到挂起问题对应的原问题中。
- 无法可靠衔接上下文或存在多个合理解释时，不要强行补全；need_user_input=true，并列出 missing_slots。
- rewritten_question 必须保留原始修饰关系、并列关系、筛选关系和业务短语边界。
""".strip()


INTENT_SYSTEM_PROMPT = """
你是 ChatBI 的分析形态、指标和时间意图识别器。不回答问题，不生成 SQL，不选择工具，也不绑定资产 ID、字段名或表名。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "intent_type": "metric_query | trend_analysis | ranking_analysis | comparison_analysis | detail_query | share_analysis | anomaly_analysis | unknown",
  "confidence": 0.0,
  "metric_mentions": [],
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "自然语言维度名", "role": "group_by | filter | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
  ],
  "time_mentions": [],
  "time_range": {"raw": null, "value_status": "provided | not_provided"},
  "filter_mentions": [],
  "required_slot_types": [],
  "query_shape": {},
  "ambiguous_slots": [],
  "conflict_slots": []
}

分析形态：
- metric_query：指标值或统计值。
- trend_analysis：趋势、走势或按时间粒度变化。
- ranking_analysis：排行、最高、最低、TopN。
- comparison_analysis：同比、环比、较上期或多个对象比较。
- detail_query：明细、列表、清单。
- share_analysis：占比、构成、比例。
- anomaly_analysis：异常、波动或变化原因。

抽取边界：
- 只抽取用户明确表达的线索，不推断、补全、改写或标准化业务口径。
- metric_mentions 只包含指标、事实或可度量业务结果，并保留“支付订单数”“新增用户数”等完整修饰关系。
- 时间表达、分组对象、筛选值、比较方式、排序、TopN、展示方式和单纯维度名不得进入 metric_mentions。
- dimension_mentions、dimension_slots 和 filter_mentions 固定输出空数组；维度与筛选由独立维度子任务识别。
- 时间表达不能作为普通维度或维度值。
- “看一下北京最近7天的数据”没有明确指标，metric_mentions 必须为空，不得猜测指标。
- 多个时间或指标表达互相冲突时，保留原文并加入 conflict_slots。
- required_slot_types 只能使用 metric、dimension、time_dimension、time_range、filter、order、limit、comparison_target。

示例：
- “按城市看本月订单数”：metric_mentions=["订单数"]，time_range.raw="本月"，维度相关字段保持空数组。
- “北京 App 端的销售额”：metric_mentions=["销售额"]，维度和筛选相关字段保持空数组。
- “最近7天的上月销售额”：metric_mentions=["销售额"]，并将冲突时间放入 conflict_slots。
""".strip()


DIMENSION_SYSTEM_PROMPT = """
你是 ChatBI 的自然语言维度槽位识别器。你只识别维度、维度用法和明确筛选条件，不回答问题，不生成 SQL，不选择工具，也不绑定资产 ID、字段名或表名。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "dimension_mentions": [],
  "dimension_slots": [
    {"name": "自然语言维度名", "role": "group_by | filter | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
  ],
  "filter_mentions": [],
  "ambiguous_slots": [],
  "conflict_slots": []
}

覆盖性原则：
- 独立检查问题中的每个显式业务对象、分析对象和限定对象，不能因为指标已经明确就省略维度线索。
- 排除明确的指标短语、时间表达、排序、比较和展示方式后，剩余业务对象如果可能承担分组或筛选作用，必须进入 dimension_mentions 和 dimension_slots。
- dimension_mentions 与 dimension_slots 必须一致，每个 mention 都必须有且只有一个同名槽位。
- 不从指标短语内部强拆“新增、支付、成交、累计”等指标修饰词作为维度。

角色规则：
- “按/各/每个/分……统计”表示 role=group_by，value=null，value_status=not_provided。
- 维度带有明确值时表示 role=filter，例如“北京的销售额”“1号门店订单数”；value 必须保留原文值并设置 value_status=provided。
- 只出现业务对象或维度名，没有“按/各/每个”等分组标记，也没有具体筛选值时，role=ambiguous，value=null，value_status=not_provided，并把维度名加入 ambiguous_slots。
- “时间 + 业务对象 + 的 + 指标”中的业务对象不能被省略；没有分组标记或具体值时仍然是 ambiguous。
- 时间表达不能作为普通维度或维度值。
- 多个维度表达互相冲突时保留原文，并加入 conflict_slots。

示例：
- “今天门店的客户数”：门店 role=ambiguous。
- “今天各门店的客户数”：门店 role=group_by。
- “今天1号门店的客户数”：门店 role=filter，value="1号"。
- “今天新增客户数”：新增属于指标修饰词，不输出维度。
""".strip()


class QuestionUnderstandingService:
    """严格执行重写、意图识别和确定性校验，不提供静默降级。"""

    def __init__(
        self,
        model_client: QuestionUnderstandingModelClient | None = None,
        validation_service: QuestionUnderstandingValidationService | None = None,
    ) -> None:
        self._model_client = model_client or DefaultQuestionUnderstandingModelClient()
        self._validation_service = validation_service or QuestionUnderstandingValidationService()

    def understand(
        self,
        *,
        question: str,
        datasource_id: int | None,
        conversation_context: dict[str, Any] | None = None,
    ) -> QuestionUnderstandingOutcome:
        context = conversation_context or {}
        rewrite_response = self._model_client.invoke(
            REWRITE_SYSTEM_PROMPT,
            orjson.dumps(
                {
                    "question": question,
                    "datasource_id": datasource_id,
                    "conversation_context": context,
                }
            ).decode(),
        )
        rewrite = _validate_model_json(rewrite_response.content, QuestionRewriteOutput, "QUESTION_REWRITE")

        intent_response = self._model_client.invoke(
            INTENT_SYSTEM_PROMPT,
            orjson.dumps(
                {
                    "rewritten_question": rewrite.rewritten_question,
                    "inherited_context": rewrite.inherited_context,
                }
            ).decode(),
        )
        intent = _validate_model_json(intent_response.content, IntentRecognitionOutput, "INTENT_RECOGNITION")
        dimension_response = self._model_client.invoke(
            DIMENSION_SYSTEM_PROMPT,
            orjson.dumps(
                {
                    "rewritten_question": rewrite.rewritten_question,
                    "metric_mentions": intent.metric_mentions,
                    "time_mentions": intent.time_mentions,
                    "inherited_context": rewrite.inherited_context,
                }
            ).decode(),
        )
        dimensions = _validate_model_json(
            dimension_response.content,
            DimensionRecognitionOutput,
            "DIMENSION_RECOGNITION",
        )
        intent = intent.model_copy(
            update={
                "time_range": TimeRange.model_validate(
                    normalize_time_range_payload(intent.time_range.model_dump(mode="json"))
                ),
                "dimension_mentions": dimensions.dimension_mentions,
                "dimension_slots": dimensions.dimension_slots,
                "filter_mentions": dimensions.filter_mentions,
                "ambiguous_slots": _unique_strings(
                    [*intent.ambiguous_slots, *dimensions.ambiguous_slots]
                ),
                "conflict_slots": _unique_strings(
                    [*intent.conflict_slots, *dimensions.conflict_slots]
                ),
            }
        )
        validation = _validate_understanding(self._validation_service, rewrite, intent)
        output = QuestionUnderstandingOutput(
            original_question=question,
            message_type=rewrite.message_type,
            rewritten_question=rewrite.rewritten_question,
            inherited_context=rewrite.inherited_context,
            intent=intent,
            validation=validation,
        )
        return QuestionUnderstandingOutcome(
            output=output,
            usage_metadata=_merge_usage(
                rewrite_response.usage_metadata,
                intent_response.usage_metadata,
                dimension_response.usage_metadata,
            ),
        )

def apply_question_understanding_clarification(
    *,
    understanding: dict[str, Any],
    resume_payload: dict[str, Any],
    answer: dict[str, Any],
) -> QuestionUnderstandingOutput:
    """将用户回答定点写回既有问题理解，不重新调用问题理解模型。"""

    try:
        previous = QuestionUnderstandingOutput.model_validate(understanding)
    except ValidationError as exc:
        raise QuestionUnderstandingError(f"CLARIFICATION_CHECKPOINT_INVALID: {exc}") from exc

    operation = str(resume_payload.get("operation") or "")
    slot_name = str(resume_payload.get("slot_name") or "").strip()
    if operation not in {"set_dimension_role", "set_dimension_filter_value"} or not slot_name:
        raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")

    intent = previous.intent
    slots = list(intent.dimension_slots)
    matching_indexes = [index for index, slot in enumerate(slots) if slot.name == slot_name]
    if len(matching_indexes) != 1:
        raise QuestionUnderstandingError("CLARIFICATION_DIMENSION_SLOT_NOT_FOUND")
    slot_index = matching_indexes[0]
    slot = slots[slot_index]
    ambiguous_slots = [item for item in intent.ambiguous_slots if item != slot_name]

    if operation == "set_dimension_role":
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
            dimension_mentions = [item for item in intent.dimension_mentions if item != slot_name]
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
    else:
        filter_value = _clarification_answer_value(answer)
        slots[slot_index] = slot.model_copy(
            update={
                "role": "filter",
                "value": filter_value,
                "value_status": "provided",
                "value_confidence": 1.0,
            }
        )
        dimension_mentions = intent.dimension_mentions

    updated_intent = intent.model_copy(
        update={
            "dimension_mentions": dimension_mentions,
            "dimension_slots": slots,
            "ambiguous_slots": ambiguous_slots,
        }
    )
    # 重写结果已经在挂起前确定；这里只重新执行无模型副作用的业务校验。
    rewrite = QuestionRewriteOutput(
        message_type=previous.message_type,
        rewritten_question=previous.rewritten_question,
        inherited_context=previous.inherited_context,
        confidence=1.0,
    )
    validation = _validate_understanding(
        QuestionUnderstandingValidationService(),
        rewrite,
        updated_intent,
    )
    return previous.model_copy(update={"intent": updated_intent, "validation": validation})


def _validate_understanding(
    service: QuestionUnderstandingValidationService,
    rewrite: QuestionRewriteOutput,
    intent: IntentRecognitionOutput,
) -> IntentValidationOutput:
    result = service.validate(
        QuestionUnderstandingValidationData(
            rewrite_need_user_input=rewrite.need_user_input,
            rewrite_missing_slots=tuple(rewrite.missing_slots),
            intent_type=intent.intent_type,
            metric_mentions=tuple(intent.metric_mentions),
            dimension_slots=tuple(
                slot.model_dump(mode="json") for slot in intent.dimension_slots
            ),
            time_range=intent.time_range.model_dump(mode="json"),
            ambiguous_slots=tuple(intent.ambiguous_slots),
            conflict_slots=tuple(intent.conflict_slots),
        )
    )
    return IntentValidationOutput(
        status="clarification_required" if result.issues else "valid",
        reason_codes=result.reason_codes,
        clarification_slots=result.clarification_slots,
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


ModelType = TypeVar("ModelType", bound=BaseModel)


def _validate_model_json(content: str, model_type: type[ModelType], stage: str) -> ModelType:
    try:
        payload = orjson.loads(content)
    except orjson.JSONDecodeError as exc:
        raise QuestionUnderstandingError(f"{stage}_MODEL_OUTPUT_NOT_JSON") from exc
    if not isinstance(payload, dict):
        raise QuestionUnderstandingError(f"{stage}_MODEL_OUTPUT_NOT_OBJECT")
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise QuestionUnderstandingError(f"{stage}_MODEL_OUTPUT_INVALID: {exc}") from exc


def _message_content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    return ""


def _merge_usage(*items: dict[str, Any]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "total_tokens")
    return {key: sum(int(item.get(key) or 0) for item in items) for key in keys}


def _unique_strings(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
