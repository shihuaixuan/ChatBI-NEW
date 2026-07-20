"""ChatBI 问题理解应用服务。"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

import orjson
from pydantic import BaseModel, ValidationError

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
from apps.chatbi.services.question_model_service import (
    QuestionModelCallError,
    QuestionModelError,
    QuestionModelOutputError,
    QuestionModelService,
)
from apps.chatbi.services.question_understanding_prompt import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.chatbi.services.question_understanding_validation_service import (
    QuestionUnderstandingValidationService,
)
from apps.chatbi.services.time_range import normalize_time_range_payload


class QuestionUnderstandingError(RuntimeError):
    """问题理解阶段失败，调用方应明确终止当前问数流程。"""


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
  "filter_mentions": [],
  "ambiguous_slots": [],
  "conflict_slots": []
}
""".strip(),
        DIMENSION_EXTRACTION_RULES,
        """
Agent 示例：
- “时间 + 业务对象 + 的 + 指标”中的业务对象不能被省略；没有分组标记或具体值时仍然是 ambiguous。

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
        validation_service: QuestionUnderstandingValidationService | None = None,
        question_model_service: QuestionModelService | None = None,
    ) -> None:
        if model_client is not None and question_model_service is not None:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SOURCE_CONFLICT")
        if model_client is not None:
            self._question_model_service = QuestionModelService(model_client)
        elif question_model_service is not None:
            self._question_model_service = question_model_service
        else:
            raise ValueError("QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED")
        self._validation_service = validation_service or QuestionUnderstandingValidationService()

    def understand(
        self,
        *,
        question: str,
        datasource_id: int | None,
        conversation_context: dict[str, Any] | None = None,
    ) -> QuestionUnderstandingOutcome:
        context = conversation_context or {}
        rewrite_response = self._invoke_model(
            "QUESTION_REWRITE",
            REWRITE_SYSTEM_PROMPT,
            orjson.dumps(
                {
                    "question": question,
                    "datasource_id": datasource_id,
                    "conversation_context": context,
                }
            ).decode(),
        )
        rewrite = _validate_model_payload(
            rewrite_response.payload,
            QuestionRewriteOutput,
            "QUESTION_REWRITE",
        )

        intent_response = self._invoke_model(
            "INTENT_RECOGNITION",
            INTENT_SYSTEM_PROMPT,
            orjson.dumps(
                {
                    "rewritten_question": rewrite.rewritten_question,
                    "inherited_context": rewrite.inherited_context,
                }
            ).decode(),
        )
        intent = _validate_model_payload(
            intent_response.payload,
            IntentRecognitionOutput,
            "INTENT_RECOGNITION",
        )
        dimension_response = self._invoke_model(
            "DIMENSION_RECOGNITION",
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
        dimensions = _validate_model_payload(
            dimension_response.payload,
            DimensionRecognitionOutput,
            "DIMENSION_RECOGNITION",
        )
        intent = intent.model_copy(
            update={
                "time_range": TimeRange.model_validate(
                    normalize_time_range_payload(
                        intent.time_range.model_dump(mode="json")
                    )
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


def _validate_model_payload(
    payload: dict[str, Any],
    model_type: type[ModelType],
    stage: str,
) -> ModelType:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise QuestionUnderstandingError(f"{stage}_MODEL_OUTPUT_INVALID: {exc}") from exc


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
