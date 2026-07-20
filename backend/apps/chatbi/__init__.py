"""ChatBI 核心领域。

领域公共面（AGENTS.md v2 §8.1）：其他模块只应使用本文件导出的符号，
以及 `apps.chatbi.composition` 的 `build_*` 组装入口与 `apps.chatbi.models` 的公开 DTO。
services 子包内部结构对外不承诺稳定。

公共面按 PEP 562 惰性解析：旧 Chat 兼容层（`apps.chat.models.chat_model`，台账 B2）仍会在
其他领域的初始化链路中导入本包，公共面在 import 期加载全部子域会放大该历史链路；
这不是掩盖领域间循环（依赖方向本身合法），R3-d 删除 B2 后可改回直接导入。
"""

from importlib import import_module
from typing import Any

_SERVICES = "apps.chatbi.services"
_ERRORS = "apps.chatbi.errors"

_PUBLIC: dict[str, str] = {
    "AnalysisPredictionService": _SERVICES,
    "AnswerGenerationService": _SERVICES,
    "ChatBIError": _ERRORS,
    "ChartGenerationService": _SERVICES,
    "ChatRecordService": _SERVICES,
    "ConversationService": _SERVICES,
    "DatasourceSelectionService": _SERVICES,
    "GenerationContextService": _SERVICES,
    "GuardedQueryService": _SERVICES,
    "PhysicalSchemaService": _SERVICES,
    "QueryResultProjectionService": _SERVICES,
    "QuestionUnderstandingService": _SERVICES,
    "RecommendedQuestionService": _SERVICES,
    "ResultArtifactService": _SERVICES,
    "SQLGenerationService": _SERVICES,
    "SQLPermissionService": _SERVICES,
    "SchemaContextService": _SERVICES,
    "SemanticCompilationService": _SERVICES,
    "SemanticRetrievalService": _SERVICES,
    "apply_question_understanding_clarification": _SERVICES,
    "normalize_chat_record_status": _SERVICES,
    "project_answer_context": _SERVICES,
    "project_final_reply": _SERVICES,
    "project_query_final_reply": _SERVICES,
    "resolve_execution_binding": _SERVICES,
    "resolve_generation_scope": _SERVICES,
}

__all__ = sorted(_PUBLIC)


def __getattr__(name: str) -> Any:
    module_path = _PUBLIC.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC))
