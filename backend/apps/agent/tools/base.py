"""Agentic 工具基座。

工具 = pydantic 参数 schema（喂给 bind_tools 供 LLM 选择）+ execute 实现。
执行永远由 AgentLoop 经白名单分发，LLM 只能提名工具与参数。

返回统一为 ToolOutput：
- summary：回写进 LLM 消息历史（ToolMessage），受字数上限约束；
- payload：完整结构化结果，落库/给前端，不进上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import orjson
from pydantic import BaseModel

if TYPE_CHECKING:
    from apps.chatbi.models import (
        PhysicalSchemaResult,
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

    def execute_sql(
        self,
        *,
        sql: str,
        datasource_id: int,
        workspace_id: int | None,
        user_id: int | None,
        allowed_tables: list[str] | None = None,
    ) -> Any: ...


@dataclass
class AgentToolContext:
    """一次 run 的执行上下文（由 API 层组装，工具只读）。"""

    session: Any
    oid: int
    user_id: int | None
    datasource_id: int | None
    dataset_id: int | None = None
    term_query_service: TermQueryService | None = None
    query_service: QueryService | None = None
    semantic_query_service: SemanticQueryCompiler | None = None
    semantic_retrieval_service: SemanticAssetRetriever | None = None
    physical_schema_service: PhysicalSchemaReader | None = None
    config: Any = None
    # 循环内跨工具共享的运行时状态（语义包、执行结果标记等），由 loop 维护。
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutput:
    success: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None


class AgentTool:
    """工具基类：子类声明 name/description/args_model 并实现 execute。"""

    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    def execute(self, ctx: AgentToolContext, args: BaseModel) -> ToolOutput:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def tool_spec(cls) -> dict[str, Any]:
        """OpenAI function-calling 形态的工具定义，供 bind_tools 使用。"""

        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.args_model.model_json_schema(),
            },
        }


def truncate_summary(summary: str, max_chars: int) -> str:
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars] + f"\n…(截断，完整结果已存档，原文 {len(summary)} 字符)"


def json_summary(data: Any, max_chars: int) -> str:
    return truncate_summary(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode(), max_chars)
