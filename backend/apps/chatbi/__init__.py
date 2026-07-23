"""ChatBI 核心领域。

领域公共面（AGENTS.md v2 §8.1）：其他模块只应使用本文件导出的符号，
以及 `apps.chatbi.composition` 的 `build_*` 组装入口与 `apps.chatbi.models` 的公开 DTO。
services 子包内部结构对外不承诺稳定。

公共面按 PEP 562 惰性解析，避免使用方仅导入一个公开对象时加载全部子域。
"""

from importlib import import_module
from typing import Any

_CONVERSATION = "apps.chatbi.services.conversation"
_EXECUTION = "apps.chatbi.services.execution"
_GENERATION = "apps.chatbi.services.generation"
_PLANNING = "apps.chatbi.services.planning"
_UNDERSTANDING = "apps.chatbi.services.understanding"
_ERRORS = "apps.chatbi.errors"
_LEGACY_READ = "apps.chatbi.api.legacy_read"
_RESOURCE_SCOPE = "apps.chatbi.resource_scope"

_PUBLIC: dict[str, str] = {
    "AnalysisPredictionService": _GENERATION,
    "AnswerGenerationService": _GENERATION,
    "ChatBIError": _ERRORS,
    "ChartGenerationService": _GENERATION,
    "ChatWorkspaceResourceScopeReader": _RESOURCE_SCOPE,
    "ChatRecordService": _CONVERSATION,
    "ConversationService": _CONVERSATION,
    "DatasourceSelectionService": _PLANNING,
    "GenerationContextService": _GENERATION,
    "GuardedQueryService": _EXECUTION,
    "PhysicalSchemaService": _PLANNING,
    "QueryResultProjectionService": _EXECUTION,
    "QuestionUnderstandingService": _UNDERSTANDING,
    "RecommendedQuestionService": _GENERATION,
    "ResultArtifactService": _EXECUTION,
    "SQLGenerationService": _GENERATION,
    "SQLPermissionService": _EXECUTION,
    "SchemaContextService": _GENERATION,
    "SemanticCompilationService": _PLANNING,
    "SemanticRetrievalService": _PLANNING,
    "apply_question_understanding_clarification": _UNDERSTANDING,
    "normalize_chat_record_status": _CONVERSATION,
    "project_answer_context": _GENERATION,
    "project_final_reply": _GENERATION,
    "project_query_final_reply": _GENERATION,
    "resolve_execution_binding": _PLANNING,
    "resolve_generation_scope": _GENERATION,
    "get_chart_data_ds": _LEGACY_READ,
}

__all__ = sorted(_PUBLIC)


def __getattr__(name: str) -> Any:
    module_path = _PUBLIC.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC))
