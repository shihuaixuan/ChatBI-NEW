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
        ResultArtifactWriteData,
        SemanticQueryCompileData,
        SemanticQueryCompileResult,
        SemanticRetrievalData,
    )
    from apps.datasource import (
        DatasourceQueryRequest,
        DatasourceQueryResult,
    )


class QueryService(Protocol):
    """Agent 对 Datasource 安全查询服务的最小依赖。"""

    def validate(self, request: DatasourceQueryRequest) -> DatasourceQueryResult: ...

    def execute(self, request: DatasourceQueryRequest) -> DatasourceQueryResult: ...

    def resolve_policy(self, subject, datasource_id): ...


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

    def filter_authorized_tables(
        self,
        package: dict[str, Any],
        authorized_tables: list[str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentToolContextServices:
    """创建工具执行上下文所需的稳定服务集合。"""

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
    result_artifact_service: ResultArtifactWriter | None = None
    config: Any = None
    # 循环内跨工具共享的运行时状态（语义包、执行结果标记等），由 loop 维护。
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_id(self) -> int:
        return self.oid

    @property
    def selected_tables(self) -> list[str]:
        """把 ChatBI 派生状态投影成公共 Tool 的可信选表范围。"""

        return [
            str(table)
            for table in self.state.get("allowed_tables") or []
            if str(table).strip()
        ]

    @property
    def summary_max_chars(self) -> int:
        return int(getattr(self.config, "summary_max_chars", 4000) or 4000)


class AgentTool(Tool):
    """ChatBI 领域工具基类；继承通用 Tool 协议。"""


__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolContextServices",
]
