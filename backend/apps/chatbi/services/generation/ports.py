"""生成子域端口（AGENTS.md v2 §5：可替换技术缝）。

`GenerationModelClient` 是所有生成能力共享的模型流端口；各能力仅保留自己的
PromptBuilder。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from apps.chatbi.models.dto.analysis_prediction import AnalysisPredictionGenerationData
from apps.chatbi.models.dto.chart_generation import ChartGenerationData
from apps.chatbi.models.dto.datasource_selection import DatasourceSelectionData
from apps.chatbi.models.dto.dynamic_sql_generation import DynamicSQLGenerationData
from apps.chatbi.models.dto.permission_sql_generation import PermissionSQLGenerationData
from apps.chatbi.models.dto.recommended_question import (
    RecommendedQuestionGenerationData,
)
from apps.chatbi.models.dto.sql_generation import SQLGenerationData
from apps.chatbi.models.dto.streaming import ModelMessage, ModelStreamChunk


class GenerationModelClient(Protocol):
    """生成能力共享的流式模型端口（真实 LangChain 客户端 / 测试替身）。"""

    def stream(
        self,
        messages: list[ModelMessage],
    ) -> Iterator[ModelStreamChunk]: ...


class SQLGenerationPromptBuilder(Protocol):
    def build(self, data: SQLGenerationData) -> list[ModelMessage]: ...


class DynamicSQLGenerationPromptBuilder(Protocol):
    def build(self, data: DynamicSQLGenerationData) -> list[ModelMessage]: ...


class PermissionSQLGenerationPromptBuilder(Protocol):
    def build(self, data: PermissionSQLGenerationData) -> list[ModelMessage]: ...


class ChartGenerationPromptBuilder(Protocol):
    def build(self, data: ChartGenerationData) -> list[ModelMessage]: ...


class AnalysisPredictionPromptBuilder(Protocol):
    def build(
        self,
        data: AnalysisPredictionGenerationData,
    ) -> list[ModelMessage]: ...


class RecommendedQuestionPromptBuilder(Protocol):
    def build(
        self,
        data: RecommendedQuestionGenerationData,
        old_questions: list[str],
    ) -> list[ModelMessage]: ...


class DatasourceSelectionPromptBuilder(Protocol):
    def build(self, data: DatasourceSelectionData) -> list[ModelMessage]: ...


class RecommendedQuestionHistoryRepository(Protocol):
    def list_recent(
        self,
        datasource_id: int | None,
        *,
        limit: int = 20,
    ) -> list[str]: ...


__all__ = [
    "AnalysisPredictionPromptBuilder",
    "ChartGenerationPromptBuilder",
    "DatasourceSelectionPromptBuilder",
    "DynamicSQLGenerationPromptBuilder",
    "GenerationModelClient",
    "PermissionSQLGenerationPromptBuilder",
    "RecommendedQuestionHistoryRepository",
    "RecommendedQuestionPromptBuilder",
    "SQLGenerationPromptBuilder",
]
