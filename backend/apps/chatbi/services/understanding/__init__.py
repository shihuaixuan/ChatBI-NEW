"""问题理解子域：共享的重写/意图/维度理解、确定性规则与 Graph 契约投影。

模块导航：
- understanding_service：Agent 直用的严格三阶段理解编排（Service）
- model_invocation：结构化模型调用边界（StructuredModelService + QuestionModelClient 端口）
- validation：确定性校验规则（共享函数）
- intent_projection：意图清洗/合并投影（共享函数）
- intent_fallback：模型不可用时的规则降级（Service，规则密集）
- graph_contracts：Graph 专用契约投影函数
- prompts / time_range：共享提示词规则与时间表达规则
"""

from apps.chatbi.services.understanding.graph_contracts import (
    DEFAULT_MAX_INTENT_RETRY,
    classification_precondition,
    empty_rewrite,
    fallback_rewrite,
    intent_retry_feedback,
    project_classification,
    project_rewrite,
    validate_intent,
)
from apps.chatbi.services.understanding.intent_fallback import (
    QuestionIntentFallbackService,
)
from apps.chatbi.services.understanding.intent_projection import (
    normalize_confidence,
    normalize_dimension_role,
    normalize_intent_type,
    normalize_required_slot_types,
    normalize_semantic,
    normalize_shape,
    normalize_text_list,
    normalize_value_status,
    project_question_intent,
    time_range_from_mentions,
    unique_strings,
)
from apps.chatbi.services.understanding.model_invocation import (
    QuestionModelClient,
    QuestionModelService,
    StructuredModelService,
)
from apps.chatbi.services.understanding.prompts import (
    DIMENSION_EXTRACTION_RULES,
    METRIC_TIME_EXTRACTION_RULES,
    QUESTION_REWRITE_BUSINESS_RULES,
)
from apps.chatbi.services.understanding.time_range import (
    is_time_expression,
    normalize_time_range,
    normalize_time_range_payload,
)
from apps.chatbi.services.understanding.understanding_service import (
    DIMENSION_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    QuestionUnderstandingModelClient,
    QuestionUnderstandingModelResponse,
    QuestionUnderstandingService,
    apply_question_understanding_clarification,
)
from apps.chatbi.services.understanding.validation import (
    validate_question_understanding,
)

__all__ = [
    "DEFAULT_MAX_INTENT_RETRY",
    "DIMENSION_EXTRACTION_RULES",
    "DIMENSION_SYSTEM_PROMPT",
    "INTENT_SYSTEM_PROMPT",
    "METRIC_TIME_EXTRACTION_RULES",
    "QUESTION_REWRITE_BUSINESS_RULES",
    "QuestionIntentFallbackService",
    "QuestionModelClient",
    "QuestionModelService",
    "QuestionUnderstandingModelClient",
    "QuestionUnderstandingModelResponse",
    "QuestionUnderstandingService",
    "REWRITE_SYSTEM_PROMPT",
    "StructuredModelService",
    "apply_question_understanding_clarification",
    "classification_precondition",
    "empty_rewrite",
    "fallback_rewrite",
    "intent_retry_feedback",
    "is_time_expression",
    "normalize_confidence",
    "normalize_dimension_role",
    "normalize_intent_type",
    "normalize_required_slot_types",
    "normalize_semantic",
    "normalize_shape",
    "normalize_text_list",
    "normalize_time_range",
    "normalize_time_range_payload",
    "normalize_value_status",
    "project_classification",
    "project_question_intent",
    "project_rewrite",
    "time_range_from_mentions",
    "unique_strings",
    "validate_intent",
    "validate_question_understanding",
]
