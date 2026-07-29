"""ChatBI 核心领域。

领域公共面（AGENTS.md v2 §8.1）：其他模块只应使用本文件导出的符号，
以及 `apps.chatbi.composition` 的 `build_*` 组装入口与 `apps.chatbi.models` 的公开 DTO。
services 子包内部结构对外不承诺稳定。

公共面按 PEP 562 惰性解析，避免使用方仅导入一个公开对象时加载全部子域。
"""

from importlib import import_module
from typing import Any

_EXECUTION = "apps.chatbi.services.execution"
_GENERATION = "apps.chatbi.services.generation"
_PLANNING = "apps.chatbi.services.planning"
_UNDERSTANDING = "apps.chatbi.services.understanding"
_ERRORS = "apps.chatbi.errors"

_PUBLIC: dict[str, str] = {
    "AnalysisPredictionService": _GENERATION,
    "AnswerGenerationService": _GENERATION,
    "ChatBIError": _ERRORS,
    "ChartGenerationService": _GENERATION,
    "DatasourceSelectionService": _PLANNING,
    "GenerationContextService": _GENERATION,
    "PhysicalSchemaService": _PLANNING,
    "QueryResultProjectionService": _EXECUTION,
    "QuestionUnderstandingService": _UNDERSTANDING,
    "RecommendedQuestionService": _GENERATION,
    "ResultArtifactService": _EXECUTION,
    "SQLGenerationService": _GENERATION,
    "SchemaContextService": _GENERATION,
    "apply_question_understanding_clarification": _UNDERSTANDING,
    "project_answer_context": _GENERATION,
    "project_final_reply": _GENERATION,
    "project_query_final_reply": _GENERATION,
    "resolve_execution_binding": _PLANNING,
    "resolve_generation_scope": _GENERATION,
}

__all__ = sorted(_PUBLIC)


def __getattr__(name: str) -> Any:
    module_path = _PUBLIC.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PUBLIC))
