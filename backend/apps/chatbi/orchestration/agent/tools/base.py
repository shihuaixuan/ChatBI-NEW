"""Agent 工具基座：ChatBI 执行上下文 + 领域工具基类。

通用运行时（Tool/ToolResult/Registry）在 apps.tool。
本模块只保留问数执行上下文与服务端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from apps.tool import Tool

if TYPE_CHECKING:
    from apps.chatbi.models import (
        ChatBIResultArtifactRef,
        PhysicalSchemaResult,
        ResultArtifactWriteData,
        SemanticQueryCompileData,
        SemanticQueryCompileResult,
        SemanticRetrievalData,
    )
    from apps.semantic.models.dto import TermSearchResult


class TermQueryService(Protocol):
    """Agent 对 Semantic 术语查询公开能力的最小依赖。"""

    def search(
        self,
        oid: int,
        dataset_id: int,
        query: str,
        limit: int = 10,
    ) -> list[TermSearchResult]: ...


class QueryService(Protocol):
    """Agent 对 ChatBI 查询服务的最小依赖。"""

    def validate_sql(
        self,
        sql: str,
        *,
        allowed_tables: list[str] | None = None,
    ) -> Any: ...

    def execute_sql(
        self,
        *,
        sql: str,
        datasource_id: int,
        workspace_id: int | None,
        user_id: int | None,
        allowed_tables: list[str] | None = None,
    ) -> Any: ...


class ResultArtifactWriter(Protocol):
    """Agent 对 ChatBI 结果 Artifact 服务的最小依赖。"""

    def save(
        self,
        data: ResultArtifactWriteData,
    ) -> ChatBIResultArtifactRef: ...


class SemanticQueryCompiler(Protocol):
    """Agent 对 ChatBI 语义 SQL 编译入口的最小依赖。"""

    def compile(
        self,
        data: SemanticQueryCompileData,
    ) -> SemanticQueryCompileResult: ...


class SemanticAssetRetriever(Protocol):
    """Agent 对 ChatBI 语义资产检索入口的最小依赖。"""

    def retrieve_for_agent(
        self,
        data: SemanticRetrievalData,
        *,
        max_candidates_per_group: int = 5,
    ) -> dict[str, Any]: ...


class PhysicalSchemaReader(Protocol):
    """Agent 对 ChatBI 物理 Schema 入口的最小依赖。"""

    def get(
        self,
        datasource_id: int,
        *,
        table_keyword: str = "",
    ) -> PhysicalSchemaResult: ...


@dataclass(frozen=True)
class AgentToolContextServices:
    """创建工具执行上下文所需的稳定服务集合。"""

    term_query_service: TermQueryService
    query_service: QueryService
    semantic_query_service: SemanticQueryCompiler
    semantic_retrieval_service: SemanticAssetRetriever
    physical_schema_service: PhysicalSchemaReader
    result_artifact_service: ResultArtifactWriter


@dataclass
class AgentToolContext:
    """一次 run 的执行上下文（由 API/Loop 组装，工具只读）。"""

    session: Any
    oid: int
    user_id: int | None
    datasource_id: int | None
    execution_id: str | None = None
    chat_id: int | None = None
    record_id: int | None = None
    dataset_id: int | None = None
    term_query_service: TermQueryService | None = None
    query_service: QueryService | None = None
    semantic_query_service: SemanticQueryCompiler | None = None
    semantic_retrieval_service: SemanticAssetRetriever | None = None
    physical_schema_service: PhysicalSchemaReader | None = None
    result_artifact_service: ResultArtifactWriter | None = None
    config: Any = None
    # 循环内跨工具共享的运行时状态（语义包、执行结果标记等），由 loop 维护。
    state: dict[str, Any] = field(default_factory=dict)


class AgentTool(Tool):
    """ChatBI 领域工具基类；继承通用 Tool 协议。"""


__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolContextServices",
]
