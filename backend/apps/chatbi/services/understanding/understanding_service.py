"""ChatBI 问题理解应用服务。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from apps.chatbi.errors import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
    QuestionUnderstandingError,
)
from apps.chatbi.models.dto.question_model import (
    QuestionModelInvocationData,
    QuestionModelResponse,
    QuestionModelResult,
)
from apps.chatbi.models.dto.question_understanding import (
    DimensionRecognitionOutput,
    IntentRecognitionOutput,
    IntentValidationOutput,
    QuestionRewriteOutput,
    QuestionUnderstandingOutcome,
    QuestionUnderstandingOutput,
    QuestionUnderstandingValidationData,
    TimeRange,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    dimension_candidate_by_text,
    dimension_candidate_from_schema_element,
    dimension_text_key,
    normalize_dimension_candidates,
)
from apps.chatbi.services.understanding.model_invocation import StructuredModelService
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.chatbi.services.understanding.validation import (
    validate_question_understanding,
)
from apps.semantic import normalize_time_range_payload
from apps.semantic.services.schema_service import DatasetSchemaProvider

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
你是 ChatBI 的问题上下文化重写器。你只负责判断当前输入与会话上下文的关系，并将其重写为语义完整、可独立理解的问题。

不识别资产 ID、字段名、表名或指标口径。

只输出以下 JSON 对象，不要输出 Markdown 或解释：
{
  "message_type": "new_question | followup | clarification_reply",
  "rewritten_question": "补全上下文后的完整问题",
  "inherited_context": {},
  "need_user_input": false,
  "missing_slots": [],
  "confidence": 0.0
}
""".strip(),
        QUESTION_REWRITE_BUSINESS_RULES,
        """
Agent 上下文规则：
- 当前输入本身构成完整问题时，message_type=new_question。
- conversation_context.last_rewritten_question 只表示最近一次成功执行后的完整问题。
- “那上个月呢”“换成订单数”等依赖上文的表达属于 followup，只从 last_rewritten_question 继承本轮缺失的语义。
- 当前输入明显在回答挂起澄清时，message_type=clarification_reply。
""".strip(),
    ]
)


INTENT_SYSTEM_PROMPT = "\n\n".join(
    [
        """
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
""".strip(),
        METRIC_TIME_EXTRACTION_RULES,
        """
Agent 输出约束：
- dimension_mentions、dimension_slots 和 filter_mentions 固定输出空数组；维度与筛选由独立维度子任务识别。
- required_slot_types 只能使用 metric、dimension、time_dimension、time_range、filter、order、limit、comparison_target。
- metric_mentions 必须保留完整业务修饰关系，优先输出用户原文中的最长完整指标短语，不能把“客户当日GMV”缩短成“GMV”。

示例：
- “按城市看本月订单数”：metric_mentions=["订单数"]，time_range.raw="本月"，维度相关字段保持空数组。
- “北京 App 端的销售额”：metric_mentions=["销售额"]，维度和筛选相关字段保持空数组。
- “最近7天的上月销售额”：metric_mentions=["销售额"]，并将冲突时间放入 conflict_slots。
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
    {"name": "自然语言维度名", "role": "group_by | filter | ambiguous", "value": null, "value_status": "provided | not_provided | ambiguous", "value_confidence": 0.0}
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
- 已进入 dimension_slots 的筛选条件禁止重复写入 residual_filter_mentions。
- residual_filter_mentions 只保留无法归属到任何 available_dimensions 的剩余条件，每个元素必须是对象，不能输出字符串。
- “今天门店的客户数”：门店 role=ambiguous。
- “今天各门店的客户数”：门店 role=group_by。
- “今天1号门店的客户数”：门店 role=filter，value="1号"。
- “今天新增客户数”：新增属于指标修饰词，不输出维度。
""".strip(),
    ]
)


class QuestionUnderstandingService:
    """严格执行重写、意图识别和确定性校验，不提供静默降级。"""

    def __init__(
        self,
        model_client: QuestionUnderstandingModelClient | None = None,
        question_model_service: StructuredModelService | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
    ) -> None:
        if model_client is not None and question_model_service is not None:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SOURCE_CONFLICT")
        if model_client is not None:
            self._question_model_service = StructuredModelService(model_client)
        elif question_model_service is not None:
            self._question_model_service = question_model_service
        else:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED")
        self._schema_provider = schema_provider

    def understand(
        self,
        *,
        question: str,
        datasource_id: int | None,
        conversation_context: dict[str, Any] | None = None,
        tenant_id: int | None = None,
        dataset_id: int | None = None,
    ) -> QuestionUnderstandingOutcome:
        context = conversation_context or {}
        available_dimensions = self._load_dimension_candidates(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )
        rewrite, rewrite_usage = self._invoke_validated_model(
            "QUESTION_REWRITE",
            REWRITE_SYSTEM_PROMPT,
            {
                "question": question,
                "datasource_id": datasource_id,
                "conversation_context": context,
            },
            QuestionRewriteOutput,
        )

        intent, intent_usage = self._invoke_validated_model(
            "INTENT_RECOGNITION",
            INTENT_SYSTEM_PROMPT,
            {
                "rewritten_question": rewrite.rewritten_question,
                "inherited_context": rewrite.inherited_context,
            },
            IntentRecognitionOutput,
        )
        dimensions, dimension_usage = self._invoke_validated_model(
            "DIMENSION_RECOGNITION",
            DIMENSION_SYSTEM_PROMPT,
            {
                "rewritten_question": rewrite.rewritten_question,
                "metric_mentions": intent.metric_mentions,
                "time_mentions": intent.time_mentions,
                "inherited_context": rewrite.inherited_context,
                "available_dimensions": [
                    item for item in available_dimensions if not item.get("is_time")
                ],
                "time_dimensions": [
                    item for item in available_dimensions if item.get("is_time")
                ],
            },
            DimensionRecognitionOutput,
            normalizer=lambda payload: _normalize_dimension_payload(
                payload,
                available_dimensions,
            ),
        )
        intent = _stabilize_intent(
            intent.model_copy(
                update={
                    "dimension_mentions": dimensions.dimension_mentions,
                    "dimension_slots": dimensions.dimension_slots,
                    "filter_mentions": dimensions.residual_filter_mentions,
                    "ambiguous_slots": _unique_strings(
                        [*intent.ambiguous_slots, *dimensions.ambiguous_slots]
                    ),
                    "conflict_slots": _unique_strings(
                        [*intent.conflict_slots, *dimensions.conflict_slots]
                    ),
                }
            ),
            rewrite.rewritten_question,
        )
        validation = _validate_understanding(rewrite, intent)
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
                rewrite_usage,
                intent_usage,
                dimension_usage,
            ),
        )

    def _load_dimension_candidates(
        self,
        *,
        tenant_id: int | None,
        dataset_id: int | None,
    ) -> list[dict[str, Any]]:
        """读取当前语义数据集维度；已绑定数据集时加载失败必须明确终止。"""

        if tenant_id is None or tenant_id <= 0 or dataset_id is None or dataset_id <= 0:
            return []
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
        return normalize_dimension_candidates(
            [
                candidate
                for dimension in schema.dimensions
                if (candidate := dimension_candidate_from_schema_element(dimension))
                is not None
            ]
        )

    def _invoke_validated_model(
        self,
        stage: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        model_type: type[ModelType],
        *,
        normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> tuple[ModelType, dict[str, int]]:
        """复用 Graph 的修复重试方式，格式错误时把精确校验信息反馈给模型。"""

        usage_items: list[dict[str, Any]] = []
        validation_error: ValidationError | None = None
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
            try:
                response = self._invoke_model(
                    stage,
                    system_prompt,
                    orjson.dumps(current_payload).decode(),
                )
            except QuestionUnderstandingError:
                # 修复调用失败时保留第一次精确的结构校验错误，避免错误原因被覆盖。
                if validation_error is not None:
                    break
                raise
            usage_items.append(response.usage_metadata)
            payload = (
                normalizer(response.payload)
                if normalizer is not None
                else response.payload
            )
            try:
                return (
                    model_type.model_validate(payload),
                    _merge_usage(*usage_items),
                )
            except ValidationError as exc:
                validation_error = exc
                if attempt == 0:
                    continue
        assert validation_error is not None
        raise QuestionUnderstandingError(
            f"{stage}_MODEL_OUTPUT_INVALID: {validation_error}"
        ) from validation_error

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
            raise QuestionUnderstandingError(f"{stage}_MODEL_CALL_FAILED") from exc
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
        raise QuestionUnderstandingError(
            f"CLARIFICATION_CHECKPOINT_INVALID: {exc}"
        ) from exc

    operation = str(resume_payload.get("operation") or "")
    slot_name = str(resume_payload.get("slot_name") or "").strip()
    if operation not in {"set_dimension_role", "set_dimension_filter_value"} or not slot_name:
        raise QuestionUnderstandingError("CLARIFICATION_RESUME_TARGET_INVALID")

    intent = previous.intent
    slots = list(intent.dimension_slots)
    matching_indexes = [
        index for index, slot in enumerate(slots) if slot.name == slot_name
    ]
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
    validation = _validate_understanding(rewrite, updated_intent)
    return previous.model_copy(update={"intent": updated_intent, "validation": validation})


def _validate_understanding(
    rewrite: QuestionRewriteOutput,
    intent: IntentRecognitionOutput,
) -> IntentValidationOutput:
    result = validate_question_understanding(
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
) -> dict[str, Any]:
    """统一 Agent 与 Graph 的维度候选映射，并保留非法结构供重试校验。"""

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
        if name_key in candidate_by_text_with_time and name_key not in candidate_by_text:
            continue
        candidate = candidate_by_text.get(name_key)
        if candidate is not None:
            slot["name"] = candidate["name"]
            slot.pop("dimension", None)
            slot["value"] = _normalize_dimension_value(slot.get("value"), candidate)
        slots.append(slot)
    normalized["dimension_slots"] = slots

    mentions: list[str] = []
    raw_mentions = normalized.get("dimension_mentions")
    if isinstance(raw_mentions, list):
        for raw_mention in raw_mentions:
            mention = str(raw_mention or "").strip()
            mention_key = dimension_text_key(mention)
            if mention_key in candidate_by_text_with_time and mention_key not in candidate_by_text:
                continue
            candidate = candidate_by_text.get(mention_key)
            resolved = str(candidate["name"] if candidate is not None else mention)
            if resolved and resolved not in mentions:
                mentions.append(resolved)
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        name = str(slot.get("name") or "").strip()
        if name and name not in mentions:
            mentions.append(name)
    normalized["dimension_mentions"] = mentions

    slot_filter_keys = {
        (
            str(slot.get("name") or ""),
            str(slot.get("value") or ""),
        )
        for slot in slots
        if isinstance(slot, dict)
        and str(slot.get("role") or "").lower() == "filter"
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


def _normalize_dimension_value(
    value: Any,
    candidate: dict[str, Any],
) -> Any:
    """ID 类型只移除明确的维度名称和连接符，不猜测或改写业务值。"""

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
    rewritten_question: str,
) -> IntentRecognitionOutput:
    """把可确定的时间、必需槽位和查询形态统一收敛，避免交给模型重复猜测。"""

    time_range = TimeRange.model_validate(
        normalize_time_range_payload(intent.time_range.model_dump(mode="json"))
    )
    time_mentions = list(intent.time_mentions)
    if time_range.raw and time_range.raw not in time_mentions:
        time_mentions.append(time_range.raw)

    required = list(intent.required_slot_types)

    def require(slot_type: str) -> None:
        if slot_type not in required:
            required.append(slot_type)

    if intent.metric_mentions and intent.intent_type != "detail_query":
        require("metric")
    if any(slot.role == "group_by" for slot in intent.dimension_slots):
        require("dimension")
    if any(slot.role == "filter" for slot in intent.dimension_slots):
        require("filter")
    if time_range.value_status == "provided":
        require("time_dimension")

    query_shape = dict(intent.query_shape)
    query_shape.setdefault(
        "select_mode",
        "detail" if intent.intent_type == "detail_query" else "aggregate",
    )
    if intent.intent_type == "trend_analysis":
        require("metric")
        require("time_dimension")
        query_shape.setdefault("needs_group_by", True)
        time_grain = _infer_time_grain(rewritten_question)
        if time_grain:
            query_shape.setdefault("time_grain", time_grain)
    elif intent.intent_type == "ranking_analysis":
        require("metric")
        require("dimension")
        require("order")
        require("limit")
        query_shape.setdefault("needs_group_by", True)
        query_shape.setdefault("needs_order_by", True)
    elif intent.intent_type == "comparison_analysis":
        require("metric")
        require("comparison_target")
    elif intent.intent_type == "detail_query":
        require("dimension")

    return intent.model_copy(
        update={
            "time_range": time_range,
            "time_mentions": _unique_strings(time_mentions),
            "required_slot_types": required,
            "query_shape": query_shape,
        }
    )


def _infer_time_grain(question: str) -> str | None:
    if any(item in question for item in ("按天", "每天", "每日")):
        return "day"
    if any(item in question for item in ("按周", "每周")):
        return "week"
    if any(item in question for item in ("按月", "每月")):
        return "month"
    return None


def _merge_usage(*items: dict[str, Any]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "total_tokens")
    return {key: sum(int(item.get(key) or 0) for item in items) for key in keys}


def _unique_strings(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


__all__ = [
    "DIMENSION_SYSTEM_PROMPT",
    "INTENT_SYSTEM_PROMPT",
    "QuestionUnderstandingError",
    "QuestionUnderstandingModelClient",
    "QuestionUnderstandingModelResponse",
    "QuestionUnderstandingService",
    "REWRITE_SYSTEM_PROMPT",
    "apply_question_understanding_clarification",
]
