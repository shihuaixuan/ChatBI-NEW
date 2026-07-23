"""生成子域：LLM 生成能力（SQL/图表/分析/推荐/回答）与生成上下文。

流式骨架（streaming.py）与共享 DTO 统一安排在 R2。
"""

from apps.chatbi.services.generation.analysis_prediction import (
    AnalysisPredictionService,
)
from apps.chatbi.services.generation.answer_generation import (
    AnswerGenerationService,
    AnswerModelClient,
    CallableAnswerModelClient,
    build_answer_generation_prompt,
)
from apps.chatbi.services.generation.answer_projection import project_answer_context
from apps.chatbi.services.generation.chart_generation import (
    ChartGenerationError,
    ChartGenerationService,
)
from apps.chatbi.services.generation.context import (
    ADVANCED_ASSISTANT_TYPE,
    DYNAMIC_DATASOURCE_ASSISTANT_TYPES,
    PAGE_EMBEDDED_ASSISTANT_TYPE,
    GenerationContextService,
    SchemaContextService,
    SchemaRankingClient,
    project_generation_history,
    resolve_generation_scope,
    resolve_runtime_settings,
)
from apps.chatbi.services.generation.dynamic_sql_generation import (
    DynamicSQLGenerationError,
    DynamicSQLGenerationService,
)
from apps.chatbi.services.generation.final_reply import (
    FinalReplyProjectionError,
    project_final_reply,
    project_query_final_reply,
)
from apps.chatbi.services.generation.permission_sql_generation import (
    PermissionSQLGenerationError,
    PermissionSQLGenerationService,
)
from apps.chatbi.services.generation.ports import (
    AnalysisPredictionPromptBuilder,
    ChartGenerationPromptBuilder,
    DynamicSQLGenerationPromptBuilder,
    GenerationModelClient,
    PermissionSQLGenerationPromptBuilder,
    RecommendedQuestionHistoryRepository,
    RecommendedQuestionPromptBuilder,
    SQLGenerationPromptBuilder,
)
from apps.chatbi.services.generation.recommended_questions import (
    RecommendedQuestionService,
)
from apps.chatbi.services.generation.sql_generation import (
    SQLGenerationError,
    SQLGenerationService,
    parse_sql_generation_result,
)
from apps.chatbi.services.generation.streaming import (
    StreamAccumulator,
    ensure_prompt_messages,
    stream_generation,
)

__all__ = [
    "ADVANCED_ASSISTANT_TYPE",
    "AnalysisPredictionPromptBuilder",
    "AnalysisPredictionService",
    "AnswerGenerationService",
    "AnswerModelClient",
    "CallableAnswerModelClient",
    "ChartGenerationError",
    "ChartGenerationPromptBuilder",
    "ChartGenerationService",
    "DYNAMIC_DATASOURCE_ASSISTANT_TYPES",
    "DynamicSQLGenerationError",
    "DynamicSQLGenerationPromptBuilder",
    "DynamicSQLGenerationService",
    "FinalReplyProjectionError",
    "GenerationModelClient",
    "GenerationContextService",
    "SchemaRankingClient",
    "PAGE_EMBEDDED_ASSISTANT_TYPE",
    "SchemaContextService",
    "PermissionSQLGenerationError",
    "PermissionSQLGenerationPromptBuilder",
    "PermissionSQLGenerationService",
    "RecommendedQuestionHistoryRepository",
    "RecommendedQuestionPromptBuilder",
    "RecommendedQuestionService",
    "SQLGenerationError",
    "SQLGenerationPromptBuilder",
    "SQLGenerationService",
    "StreamAccumulator",
    "ensure_prompt_messages",
    "stream_generation",
    "build_answer_generation_prompt",
    "parse_sql_generation_result",
    "project_answer_context",
    "project_final_reply",
    "project_generation_history",
    "project_query_final_reply",
    "resolve_generation_scope",
    "resolve_runtime_settings",
]
