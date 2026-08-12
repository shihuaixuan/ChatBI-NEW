"""ChatBI 问题理解应用服务。"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
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

典型示例：

示例 1：可独立理解的新问题
输入：{"question":"本月新增客户数是多少？","conversation_context":{}}
输出：{"message_type":"new_question","rewritten_question":"本月新增客户数是多少？","inherited_context":{},"need_user_input":false,"missing_slots":[],"confidence":1.0}

示例 2：只替换上一轮时间的追问
输入：{"question":"那上个月呢？","conversation_context":{"last_rewritten_question":"查询本月新增客户数"}}
输出：{"message_type":"followup","rewritten_question":"查询上个月新增客户数","inherited_context":{"metric":"新增客户数"},"need_user_input":false,"missing_slots":[],"confidence":0.95}

示例 3：无法确定引用对象
输入：{"question":"那另一个呢？","conversation_context":{}}
输出：{"message_type":"followup","rewritten_question":"那另一个呢？","inherited_context":{},"need_user_input":true,"missing_slots":["context"],"confidence":0.3}
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
  "time_mentions": [],
  "time_range": {"raw": null, "value_status": "provided | not_provided"},
  "query_shape": {
    "select_mode": "aggregate | detail",
    "needs_group_by": false,
    "needs_order_by": false,
    "order_direction": "asc | desc | null",
    "limit": null,
    "time_grain": "day | week | month | quarter | year | null"
  },
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
- 不输出 dimension_mentions、dimension_slots、filter_mentions 和 required_slot_types；维度与筛选由并行的独立维度任务识别，必需槽位由服务端派生。
- metric_mentions 只提供后续语义检索所需的指标候选，不要求本阶段确定完整指标口径。
- query_shape 必须完整输出全部字段，只表示用户问题中的查询组织语义，不得绑定资产或生成 SQL。
- detail_query 的 select_mode=detail，其他意图的 select_mode=aggregate。
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
- 同一维度包含多个明确筛选值时，value 必须输出数组，例如 value=["100011", "100012"]；禁止拼接成逗号字符串。
- 已进入 dimension_slots 的筛选条件禁止重复写入 residual_filter_mentions。
- residual_filter_mentions 只保留无法归属到任何 available_dimensions 的剩余条件，每个元素必须是对象，不能输出字符串。
- “最高的5个门店”“最低的3个商品”中的门店、商品是被排名对象，role=group_by。
- “各渠道占比”中的渠道是构成维度，role=group_by。
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


class QuestionUnderstandingService:
    """严格执行重写、意图识别和确定性校验，不提供静默降级。"""

    def __init__(
        self,
        model_client: QuestionUnderstandingModelClient | None = None,
        question_model_service: StructuredModelService | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
        temporal_interpretation_service: TemporalInterpretationService | None = None,
        temporal_shadow_enabled: bool = False,
        temporal_authority_enabled: bool = False,
    ) -> None:
        if temporal_shadow_enabled and temporal_authority_enabled:
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
        temporal_enabled = temporal_shadow_enabled or temporal_authority_enabled
        if temporal_interpretation_service is not None and not temporal_enabled:
            raise ValueError("TEMPORAL_INTERPRETATION_SERVICE_DISABLED")
        self._temporal_authority_enabled = temporal_authority_enabled
        self._temporal_interpretation_service = (
            temporal_interpretation_service
            or TemporalInterpretationService(self._question_model_service)
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
        fixed_temporal_context = temporal_context or build_run_temporal_context()
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

        intent_payload = {
            "rewritten_question": rewrite.rewritten_question,
            "inherited_context": rewrite.inherited_context,
        }
        dimension_payload = {
            "rewritten_question": rewrite.rewritten_question,
            "inherited_context": rewrite.inherited_context,
            "available_dimensions": [
                item for item in available_dimensions if not item.get("is_time")
            ],
            "time_dimensions": [
                item for item in available_dimensions if item.get("is_time")
            ],
        }
        # 两个任务只依赖重写结果，并行执行可避免意图错误污染维度模型输入。
        with ThreadPoolExecutor(max_workers=2) as executor:
            intent_future = executor.submit(
                self._invoke_validated_model,
                "INTENT_RECOGNITION",
                INTENT_SYSTEM_PROMPT,
                intent_payload,
                IntentRecognitionOutput,
            )
            dimension_future = executor.submit(
                self._invoke_validated_model,
                "DIMENSION_RECOGNITION",
                DIMENSION_SYSTEM_PROMPT,
                dimension_payload,
                DimensionRecognitionOutput,
                normalizer=lambda payload: _normalize_dimension_payload(
                    payload,
                    available_dimensions,
                    rewritten_question=rewrite.rewritten_question,
                    metric_mentions=[],
                ),
                validation_fallback=_repair_dimension_coverage,
            )
            intent, intent_usage = intent_future.result()
            dimensions, dimension_usage = dimension_future.result()
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
            fixed_temporal_context,
            use_legacy_time_interpretation=not self._temporal_authority_enabled,
        )
        temporal_interpretation = None
        temporal_shadow = None
        if self._temporal_authority_enabled:
            temporal_interpretation, temporal_usage = (
                self._interpret_authoritative_plan(
                    rewritten_question=rewrite.rewritten_question,
                    intent=intent,
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
                rewritten_question=rewrite.rewritten_question,
                intent=intent,
                temporal_context=fixed_temporal_context,
                conversation_context=context,
            )
        validation = _validate_understanding(
            rewrite,
            intent,
            temporal_interpretation=temporal_interpretation,
        )
        output = QuestionUnderstandingOutput(
            original_question=question,
            message_type=rewrite.message_type,
            rewritten_question=rewrite.rewritten_question,
            inherited_context=rewrite.inherited_context,
            intent=intent,
            validation=validation,
            temporal_interpretation=temporal_interpretation,
        )
        return QuestionUnderstandingOutcome(
            output=output,
            usage_metadata=_merge_usage(
                rewrite_usage,
                intent_usage,
                dimension_usage,
                temporal_usage,
            ),
            temporal_shadow=temporal_shadow,
        )

    def _interpret_authoritative_plan(
        self,
        *,
        rewritten_question: str,
        intent: IntentRecognitionOutput,
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
        rewrite = QuestionRewriteOutput(
            message_type=previous.message_type,
            rewritten_question=previous.rewritten_question,
            inherited_context=previous.inherited_context,
            confidence=1.0,
        )
        output = previous.model_copy(
            update={
                "intent": intent,
                "validation": _validate_understanding(
                    rewrite,
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
        validation_fallback: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
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
                if attempt == 1 and validation_fallback is not None:
                    try:
                        return (
                            model_type.model_validate(validation_fallback(payload)),
                            _merge_usage(*usage_items),
                        )
                    except ValidationError as fallback_exc:
                        validation_error = fallback_exc
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
    slot_name = str(resume_payload.get("slot_name") or "").strip()
    if (
        operation not in {"set_dimension_role", "set_dimension_filter_value"}
        or not slot_name
    ):
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
    ambiguous_slots = [
        item
        for item in intent.ambiguous_slots
        if item not in {slot_name, "dimension", "filter_value"}
    ]

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

    query_shape = intent.query_shape
    if operation == "set_dimension_role":
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

    updated_intent = _stabilize_intent(
        intent.model_copy(
            update={
                "dimension_mentions": dimension_mentions,
                "dimension_slots": slots,
                "ambiguous_slots": ambiguous_slots,
                "query_shape": query_shape,
            }
        ),
        temporal_context,
        use_legacy_time_interpretation=(previous.temporal_interpretation is None),
    )
    # 重写结果已经在挂起前确定；这里只重新执行无模型副作用的业务校验。
    rewrite = QuestionRewriteOutput(
        message_type=previous.message_type,
        rewritten_question=previous.rewritten_question,
        inherited_context=previous.inherited_context,
        confidence=1.0,
    )
    validation = _validate_understanding(
        rewrite,
        updated_intent,
        temporal_interpretation=previous.temporal_interpretation,
    )
    return previous.model_copy(
        update={"intent": updated_intent, "validation": validation}
    )


def _validate_understanding(
    rewrite: QuestionRewriteOutput,
    intent: IntentRecognitionOutput,
    *,
    temporal_interpretation: TemporalInterpretationResult | None = None,
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
            query_shape=intent.query_shape.model_dump(mode="json"),
            ambiguous_slots=tuple(intent.ambiguous_slots),
            conflict_slots=tuple(intent.conflict_slots),
            temporal_plan=(
                temporal_interpretation.plan
                if temporal_interpretation is not None
                else None
            ),
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
            matched_values = _candidate_values_in_question(
                candidate,
                rewritten_question,
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
    for candidate in candidates:
        if candidate.get("is_time") or candidate["name"] in existing_slot_names:
            continue
        matched_values = _candidate_values_in_question(candidate, rewritten_question)
        if not matched_values:
            continue
        slots.append(
            {
                "name": candidate["name"],
                "role": "filter",
                "value": (
                    matched_values[0] if len(matched_values) == 1 else matched_values
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
                dimension_text_key(name) in dimension_text_key(metric)
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

    time_range = (
        TimeRange.model_validate(
            normalize_time_range_payload(
                intent.time_range.model_dump(mode="json"),
                temporal_context=temporal_context,
            )
        )
        if use_legacy_time_interpretation
        else intent.time_range
    )
    metric_internal_time_mentions = (
        {
            mention
            for mention in intent.time_mentions
            if mention != time_range.raw
            and any(
                mention != metric and mention in metric
                for metric in intent.metric_mentions
            )
        }
        if use_legacy_time_interpretation
        else set()
    )
    time_mentions = (
        [
            mention
            for mention in intent.time_mentions
            if mention not in metric_internal_time_mentions
        ]
        if use_legacy_time_interpretation
        else list(intent.time_mentions)
    )
    if time_range.raw and time_range.raw not in time_mentions:
        time_mentions.append(time_range.raw)

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
    "QuestionUnderstandingError",
    "QuestionUnderstandingModelClient",
    "QuestionUnderstandingModelResponse",
    "QuestionUnderstandingService",
    "REWRITE_SYSTEM_PROMPT",
    "apply_question_understanding_clarification",
]
