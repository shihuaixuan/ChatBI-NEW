from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

from apps.chatbi.adapters.question_model import build_question_model_service
from apps.chatbi.errors import QuestionModelCallError, QuestionModelError
from apps.chatbi.models import (
    QuestionIntentProjectionData,
    QuestionModelInvocationData,
    QuestionModelJSONMode,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    CallableQuestionModelClient,
    QuestionClassificationModelClient,
    QuestionClassificationPrompt,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    default_subject_domain as _default_subject_domain,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    int_or_none as _int_or_none,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    normalize_subject_domain_candidates as _normalize_subject_domain_candidates,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    subject_domain_payload as _subject_domain_payload,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_common import (
    valid_candidate_domain_ids as _valid_candidate_domain_ids,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_dimension import (
    build_dimension_slots_prompt,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_input import (
    build_question_classification_prompt,
    build_question_rewrite_prompt,
)
from apps.chatbi.orchestration.graph.capabilities.adapters.question_intent import (
    build_intent_recognition_prompt,
    build_intent_shape_prompt,
    build_semantic_mentions_prompt,
)
from apps.chatbi.orchestration.graph.capabilities.context import ChatBIRunContext
from apps.chatbi.orchestration.graph.schemas.v1 import IntentRecognitionOutput
from apps.chatbi.services.understanding import (
    QuestionIntentFallbackService,
    StructuredModelService,
    TemporalInterpretationService,
    apply_temporal_interpretation_payload,
    graph_contracts,
    intent_projection,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    dimension_candidate_by_text as _dimension_candidate_by_text,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    dimension_candidate_from_schema_element as _dimension_candidate_from_schema_element,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    dimension_text_key as _dimension_text_key,
)
from apps.chatbi.services.understanding.dimension_candidates import (
    normalize_dimension_candidates as _normalize_dimension_candidates,
)
from apps.semantic.services.schema_service import DatasetSchemaProvider
from apps.temporal import TemporalContext, normalize_time_range_payload

__all__ = [
    "IntentSubtaskConfig",
    "QuestionAdapter",
    "QuestionClassificationModelClient",
    "build_dimension_slots_prompt",
    "build_intent_recognition_prompt",
    "build_intent_shape_prompt",
    "build_question_classification_prompt",
    "build_question_rewrite_prompt",
    "build_semantic_mentions_prompt",
]

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


class QuestionAdapter:
    """ChatBI v1 问题节点真实能力适配器。"""

    def __init__(
        self,
        model_client: QuestionClassificationModelClient | None = None,
        schema_provider: DatasetSchemaProvider | None = None,
        intent_subtask_config: IntentSubtaskConfig | None = None,
        question_model_service: StructuredModelService | None = None,
        intent_fallback_service: QuestionIntentFallbackService | None = None,
        temporal_interpretation_service: TemporalInterpretationService | None = None,
        temporal_authority_enabled: bool = False,
    ) -> None:
        if model_client is not None and question_model_service is not None:
            raise ValueError("QUESTION_MODEL_SOURCE_CONFLICT")
        self._model_client = model_client
        self._question_model_service = (
            StructuredModelService(CallableQuestionModelClient(model_client))
            if model_client is not None
            else question_model_service or build_question_model_service()
        )
        self._schema_provider = schema_provider
        self._intent_fallback_service = (
            intent_fallback_service or QuestionIntentFallbackService()
        )
        if (
            temporal_interpretation_service is not None
            and not temporal_authority_enabled
        ):
            raise ValueError("TEMPORAL_INTERPRETATION_SERVICE_DISABLED")
        self._temporal_authority_enabled = temporal_authority_enabled
        self._temporal_interpretation_service = (
            temporal_interpretation_service
            or TemporalInterpretationService(self._question_model_service)
            if temporal_authority_enabled
            else None
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
            "all_subtasks_fallback": bool(
                self._last_intent_subtask_trace.get("all_subtasks_fallback")
            ),
            "subtasks": {
                name: dict(value)
                for name, value in dict(
                    self._last_intent_subtask_trace.get("subtasks") or {}
                ).items()
                if isinstance(value, dict)
            },
        }

    def classify(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型完成问题分类，并把输出收敛为稳定 schema。"""

        ctx = ChatBIRunContext(request)
        question = ctx.raw_question

        precondition = graph_contracts.classification_precondition(
            question,
            ctx.dataset_id,
        )
        if precondition is not None:
            return precondition

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
            return graph_contracts.project_classification(payload)
        except Exception as exc:
            raise ValueError("CLASSIFICATION_MODEL_OUTPUT_INVALID") from exc

    def rewrite(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型补全上下文，输出稳定的问题重写结果。"""

        ctx = ChatBIRunContext(request)
        question = ctx.raw_question
        if not question:
            raise ValueError("QUESTION_REWRITE_QUESTION_REQUIRED")

        prompt = build_question_rewrite_prompt(
            question=question,
            dataset_id=ctx.dataset_id,
            conversation_context=ctx.conversation,
        )
        payload = self._invoke_prompt(prompt, "rewrite")
        return graph_contracts.project_rewrite(
            payload,
            original_question=question,
        )

    def recognize_intent(self, request: dict[str, Any]) -> dict[str, Any]:
        """调用大模型分段识别分析意图，并在失败时使用轻量规则兜底。"""

        ctx = ChatBIRunContext(request)
        rewritten_question = ctx.question
        user_feedback = ctx.intent_response
        temporal_confirmation = _temporal_confirmation(ctx.slot_response)
        if (
            self._temporal_authority_enabled
            and temporal_confirmation is not None
            and _temporal_plan_unresolved(ctx.intent)
        ):
            # 时间澄清恢复只重跑时间任务，不重复问题形态、指标和维度识别。
            return self._apply_authoritative_temporal(
                dict(ctx.intent),
                rewritten_question=rewritten_question,
                temporal_context=ctx.temporal_context,
                conversation_context=ctx.conversation,
                user_feedback=user_feedback,
                user_confirmation=temporal_confirmation,
                confirmed_plan=_temporal_confirmed_plan(ctx.slot_response),
            )
        schema = self._load_dataset_schema(ctx)
        subject_domains = self._subject_domains_from_schema(schema)
        available_dimensions = self._available_dimensions_from_schema(schema)
        if not rewritten_question:
            return IntentRecognitionOutput.model_validate(
                {
                    **self._intent_fallback_service.empty_intent(),
                    "subject_domain": _default_subject_domain("not_required"),
                }
            ).model_dump(mode="json", exclude_none=True)

        conversation_context = ctx.conversation
        fallback = self._intent_fallback_service.infer(
            rewritten_question,
            include_legacy_temporal=not self._temporal_authority_enabled,
        )
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
        projection = intent_projection.project_question_intent(
            QuestionIntentProjectionData(
                shape=shape,
                semantic=semantic,
                dimensions=dimensions,
                user_feedback=user_feedback,
            )
        )
        projected_payload = dict(projection.payload)
        if self._temporal_authority_enabled:
            return self._apply_authoritative_temporal(
                projected_payload,
                rewritten_question=rewritten_question,
                temporal_context=ctx.temporal_context,
                conversation_context=conversation_context,
                user_feedback=user_feedback,
            )
        projected_payload["time_range"] = normalize_time_range_payload(
            projected_payload.get("time_range")
            or {"raw": None, "value_status": "not_provided"},
            temporal_context=ctx.temporal_context,
        )
        if projected_payload["time_range"].get("value_status") == "provided":
            projected_payload["temporal_interpretation_source"] = "jionlp"
        output = IntentRecognitionOutput.model_validate(projected_payload)
        validation = graph_contracts.validate_intent(
            output.model_dump(mode="json"),
            retry_count=0,
        )
        return output.model_copy(update={"validation": validation}).model_dump(
            mode="json",
            exclude_none=True,
        )

    def _apply_authoritative_temporal(
        self,
        intent_payload: dict[str, Any],
        *,
        rewritten_question: str,
        temporal_context: TemporalContext,
        conversation_context: dict[str, Any],
        user_feedback: dict[str, Any],
        user_confirmation: str | None = None,
        confirmed_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用共享时间任务并生成 Graph 权威意图；失败必须明确向上抛出。"""

        temporal_service = self._temporal_interpretation_service
        if temporal_service is None:
            raise RuntimeError("TEMPORAL_AUTHORITY_SERVICE_REQUIRED")
        metric_mentions = [
            str(item) for item in intent_payload.get("metric_mentions") or []
        ]
        if confirmed_plan is not None:
            if user_confirmation is None:
                raise ValueError("TEMPORAL_CONFIRMATION_TEXT_REQUIRED")
            temporal_interpretation = temporal_service.resolve_confirmed_plan(
                plan=confirmed_plan,
                rewritten_question=rewritten_question,
                metric_mentions=metric_mentions,
                temporal_context=temporal_context,
                user_confirmation=user_confirmation,
                allowed_ambiguity_codes=_temporal_ambiguity_codes(intent_payload),
            )
        else:
            temporal_interpretation, _ = temporal_service.interpret_for_execution(
                rewritten_question=rewritten_question,
                metric_mentions=metric_mentions,
                time_mentions=[
                    str(item) for item in intent_payload.get("time_mentions") or []
                ],
                temporal_context=temporal_context,
                conversation_context=conversation_context,
                # Graph 的上游意图已经识别比较关系，时间模型只补充时间区间。
                analysis_context={
                    "intent_type": intent_payload.get("intent_type"),
                    "comparison": intent_payload.get("comparison"),
                    "query_shape": intent_payload.get("query_shape") or {},
                    "expressions": intent_payload.get("expressions") or [],
                },
                user_feedback=user_feedback,
                user_confirmation=user_confirmation,
            )
        projected_payload = apply_temporal_interpretation_payload(
            intent_payload,
            temporal_interpretation,
        )
        projected_payload.update(
            {
                "temporal_plan": temporal_interpretation.plan.model_dump(mode="json"),
                "resolved_temporal_plan": (
                    temporal_interpretation.resolved_plan.model_dump(mode="json")
                    if temporal_interpretation.resolved_plan is not None
                    else None
                ),
                "temporal_interpretation_source": (
                    temporal_interpretation.interpretation_source
                ),
            }
        )
        output = IntentRecognitionOutput.model_validate(projected_payload)
        validation = graph_contracts.validate_intent(
            output.model_dump(mode="json"),
            retry_count=0,
        )
        return output.model_copy(update={"validation": validation}).model_dump(
            mode="json",
            exclude_none=True,
        )

    def _intent_subtask_fallback_payloads(
        self, fallback: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        return {
            "shape": {
                "intent_type": fallback.get("intent_type"),
                "confidence": fallback.get("confidence", 0),
                "required_slot_types": fallback.get("required_slot_types", []),
                "query_shape": fallback.get("query_shape", {}),
                "subject_domain": fallback.get("subject_domain")
                or _default_subject_domain("not_required"),
                "ambiguous_slots": fallback.get("ambiguous_slots", []),
                "conflict_slots": fallback.get("conflict_slots", []),
            },
            "semantic": {
                "metric_mentions": fallback.get("metric_mentions", []),
                "time_mentions": fallback.get("time_mentions", []),
                "time_range": (
                    normalize_time_range_payload(
                        fallback.get("time_range")
                        or {"raw": None, "value_status": "not_provided"}
                    )
                    if not self._temporal_authority_enabled
                    else {"raw": None, "value_status": "not_provided"}
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
        results: dict[str, IntentSubtaskResult]
        if not self._intent_subtask_config.enabled:
            results = {
                name: self._run_intent_subtask(name, task, fallback_payloads[name])
                for name, task in tasks.items()
            }
            self._record_intent_subtask_trace(enabled=False, results=results)
            return results

        max_workers = max(1, min(self._intent_subtask_config.max_workers, len(tasks)))
        executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="intent-subtask"
        )
        try:
            future_to_name: dict[Future[IntentSubtaskResult], str] = {
                executor.submit(
                    self._run_intent_subtask, name, task, fallback_payloads[name]
                ): name
                for name, task in tasks.items()
            }
            timeout_seconds = min(
                self._intent_subtask_config.subtask_timeout_seconds,
                self._intent_subtask_config.overall_timeout_seconds,
            )
            done, not_done = wait(future_to_name, timeout=timeout_seconds)
            results = {}
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
            logger.warning(
                "intent subtask failed; using fallback",
                extra={"subtask": name, "error_code": error_code},
            )
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
        duration_ms = (
            0 if started_at is None else int((time.monotonic() - started_at) * 1000)
        )
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
            "all_subtasks_fallback": all(
                result.status == "fallback" for result in results.values()
            ),
            "subtasks": {
                name: result.trace_payload() for name, result in results.items()
            },
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
            lambda payload: self._validate_intent_shape_payload(
                payload, subject_domains
            ),
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

        fallback_payload = self._intent_subtask_fallback_payloads(fallback)[
            "dimensions"
        ]
        return self._recognize_subtask(
            "dimension_slots",
            prompt,
            {**user_feedback},
            fallback_payload,
            lambda payload: self._validate_dimension_slots_payload(
                payload, available_dimensions
            ),
        )

    def _recognize_subtask(
        self,
        stage: str,
        prompt_builder: Callable[[dict[str, Any]], QuestionClassificationPrompt],
        user_feedback: dict[str, Any],
        fallback_payload: dict[str, Any],
        validator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = fallback_payload
        retry_feedback: dict[str, Any] = {}
        for retry_count in range(graph_contracts.DEFAULT_MAX_INTENT_RETRY):
            prompt = prompt_builder({**user_feedback, **retry_feedback})
            result = self._invoke_prompt(prompt, stage)
            validation = validator(result)
            result = validation["payload"]
            if (
                validation["status"] == "invalid"
                and validation["retryable"]
                and retry_count + 1 < graph_contracts.DEFAULT_MAX_INTENT_RETRY
            ):
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
        normalized = intent_projection.normalize_shape(
            payload,
            subject_domain=self._normalize_subject_domain_output(
                payload.get("subject_domain"),
                subject_domains,
            ),
        )
        return {
            "status": "valid",
            "retryable": False,
            "reason_code": "VALID",
            "repair_hint": None,
            "payload": normalized,
        }

    def _validate_semantic_mentions_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = intent_projection.normalize_semantic(
            payload,
            use_legacy_time_interpretation=not self._temporal_authority_enabled,
        )
        return {
            "status": "valid",
            "retryable": False,
            "reason_code": "VALID",
            "repair_hint": None,
            "payload": normalized,
        }

    def _validate_dimension_slots_payload(
        self,
        payload: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = self._normalize_dimension_slots_payload(
            payload, available_dimensions
        )
        shared_validation = graph_contracts.validate_intent(normalized)
        if shared_validation["status"] == "invalid":
            return {
                "status": "invalid",
                "retryable": bool(shared_validation["retryable"]),
                "reason_code": shared_validation["reason_code"],
                "repair_hint": shared_validation["repair_hint"],
                "payload": normalized,
            }
        violation = self._first_dimension_slot_violation(
            normalized, available_dimensions
        )
        if violation is None:
            return {
                "status": "valid",
                "retryable": False,
                "reason_code": "VALID",
                "repair_hint": None,
                "payload": normalized,
            }
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
        candidate_by_text = _dimension_candidate_by_text(
            normalized_candidates, include_time=False
        )
        candidate_by_text_with_time = _dimension_candidate_by_text(
            normalized_candidates, include_time=True
        )
        if not normalized_candidates:
            passthrough_slots = [
                dict(slot)
                for slot in payload.get("dimension_slots") or []
                if isinstance(slot, dict)
            ]
            passthrough_mentions = intent_projection.unique_strings(
                [
                    *intent_projection.normalize_text_list(
                        payload.get("dimension_mentions")
                    ),
                    *[
                        str(slot.get("name"))
                        for slot in passthrough_slots
                        if slot.get("name")
                    ],
                ]
            )
            return {
                "dimension_mentions": passthrough_mentions,
                "dimension_slots": passthrough_slots,
                "residual_filter_mentions": [
                    item
                    for item in payload.get("residual_filter_mentions") or []
                    if isinstance(item, dict)
                ],
                "ambiguous_slots": intent_projection.normalize_text_list(
                    payload.get("ambiguous_slots")
                ),
                "conflict_slots": intent_projection.normalize_text_list(
                    payload.get("conflict_slots")
                ),
            }
        mentions: list[str] = []
        for mention in intent_projection.normalize_text_list(
            payload.get("dimension_mentions")
        ):
            mention_key = _dimension_text_key(mention)
            if (
                mention_key in candidate_by_text_with_time
                and mention_key not in candidate_by_text
            ):
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
            if (
                raw_name_key in candidate_by_text_with_time
                and raw_name_key not in candidate_by_text
            ):
                continue
            candidate = candidate_by_text.get(raw_name_key)
            slot_name = candidate["name"] if candidate is not None else raw_name
            if candidate is None:
                normalized_slot = {
                    "name": slot_name,
                    "role": intent_projection.normalize_dimension_role(
                        slot.get("role")
                    ),
                    "value": slot.get("value"),
                    "value_status": intent_projection.normalize_value_status(
                        slot.get("value_status"), slot.get("value")
                    ),
                }
                if "value_confidence" in slot:
                    normalized_slot["value_confidence"] = (
                        intent_projection.normalize_confidence(
                            slot.get("value_confidence")
                        )
                    )
                slots.append(normalized_slot)
                if slot_name not in mentions:
                    mentions.append(slot_name)
                continue
            normalized_slot = {
                "name": slot_name,
                "role": intent_projection.normalize_dimension_role(slot.get("role")),
                "value": slot.get("value"),
                "value_status": intent_projection.normalize_value_status(
                    slot.get("value_status"), slot.get("value")
                ),
            }
            if "value_confidence" in slot:
                normalized_slot["value_confidence"] = (
                    intent_projection.normalize_confidence(slot.get("value_confidence"))
                )
            slots.append(normalized_slot)
            if slot_name not in mentions:
                mentions.append(slot_name)

        return {
            "dimension_mentions": mentions,
            "dimension_slots": slots,
            "residual_filter_mentions": [
                item
                for item in payload.get("residual_filter_mentions") or []
                if isinstance(item, dict)
            ],
            "ambiguous_slots": intent_projection.normalize_text_list(
                payload.get("ambiguous_slots")
            ),
            "conflict_slots": intent_projection.normalize_text_list(
                payload.get("conflict_slots")
            ),
        }

    @classmethod
    def _first_dimension_slot_violation(
        cls,
        payload: dict[str, Any],
        available_dimensions: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        candidate_by_name = {
            item["name"]: item
            for item in _normalize_dimension_candidates(available_dimensions)
        }
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
        return any(
            _dimension_text_key(text) and _dimension_text_key(text) in value_key
            for text in texts
        )

    def _load_dataset_schema(self, ctx: ChatBIRunContext) -> Any | None:
        if self._schema_provider is None:
            return None
        if ctx.dataset_id is None:
            return None
        try:
            return self._schema_provider.build_dataset_schema(
                ctx.tenant_id, ctx.dataset_id
            )
        except Exception:
            return None

    @staticmethod
    def _subject_domains_from_schema(schema: Any | None) -> list[dict[str, Any]]:
        if schema is None:
            return []
        return _normalize_subject_domain_candidates(
            getattr(schema, "subject_domains", []) or []
        )

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
    def _normalize_subject_domain_output(
        raw_subject_domain: Any,
        subject_domains: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = _normalize_subject_domain_candidates(subject_domains)
        if not candidates:
            return _default_subject_domain("not_required")

        if len(candidates) == 1:
            candidate = candidates[0]
            return _subject_domain_payload(
                candidate,
                status="not_required",
                confidence=1.0,
                reason="只有一个候选主题域",
            )

        raw = raw_subject_domain if isinstance(raw_subject_domain, dict) else {}
        candidate_by_id = {
            candidate["domain_id"]: candidate for candidate in candidates
        }
        selected_domain_id = _int_or_none(raw.get("domain_id") or raw.get("id"))
        if (
            str(raw.get("status") or "").lower() == "selected"
            and selected_domain_id is not None
            and selected_domain_id in candidate_by_id
        ):
            candidate = candidate_by_id[selected_domain_id]
            return _subject_domain_payload(
                candidate,
                status="selected",
                confidence=float(raw.get("confidence") or 0),
                reason=str(raw.get("reason") or ""),
                candidate_domain_ids=_valid_candidate_domain_ids(
                    raw.get("candidate_domain_ids"), candidate_by_id
                )
                or [selected_domain_id],
            )

        candidate_ids = _valid_candidate_domain_ids(
            raw.get("candidate_domain_ids"), candidate_by_id
        )
        if not candidate_ids:
            candidate_ids = [candidate["domain_id"] for candidate in candidates]
        status = (
            "not_matched"
            if str(raw.get("status") or "").lower() == "not_matched"
            else "ambiguous"
        )
        return {
            **_default_subject_domain(status),
            "confidence": float(raw.get("confidence") or 0),
            "reason": str(raw.get("reason") or "主题域未能唯一确定"),
            "candidate_domain_ids": candidate_ids,
        }


def _temporal_confirmation(response: dict[str, Any]) -> str | None:
    """读取时间澄清回答；没有回答时不向模型补充内容。"""

    for key in ("temporal_confirmation", "time_range", "text"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _temporal_plan_unresolved(intent: dict[str, Any]) -> bool:
    """仅未解析时间计划允许走澄清恢复快路径。"""

    plan = intent.get("temporal_plan")
    return bool(
        isinstance(plan, dict)
        and plan.get("status") in {"clarification_required", "unsupported"}
    )


def _temporal_confirmed_plan(response: dict[str, Any]) -> dict[str, Any] | None:
    """读取服务端时间选项携带的计划；自由文本回答没有该字段。"""

    plan = response.get("temporal_plan")
    return dict(plan) if isinstance(plan, dict) else None


def _temporal_ambiguity_codes(intent: dict[str, Any]) -> set[str]:
    """读取当前挂起计划允许生成澄清选项的歧义代码。"""

    plan = intent.get("temporal_plan")
    ambiguities = plan.get("ambiguities") if isinstance(plan, dict) else []
    return {
        str(item.get("code") or "")
        for item in ambiguities or []
        if isinstance(item, dict) and item.get("code")
    }
