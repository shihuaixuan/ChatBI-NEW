"""Agent 工具基座：ChatBI 执行上下文 + 领域工具基类。

通用运行时（Tool/ToolResult/Registry）在 apps.tool。
本模块只保留问数执行上下文与服务端口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from apps.retrieval import RetrievalRequest, build_semantic_binding_request
from apps.temporal import TemporalContext
from apps.tool import Tool
from apps.tool.tools.semantic_contracts import SemanticAssetScope

if TYPE_CHECKING:
    from apps.chatbi.models import (
        ChatBIResultArtifactRef,
        ResultArtifactWriteData,
    )
    from apps.chatbi.services.execution.result_store import ResultStore


class ResultArtifactWriter(Protocol):
    """Agent 对 ChatBI 结果 Artifact 服务的最小依赖。"""

    def save(
        self,
        data: ResultArtifactWriteData,
    ) -> ChatBIResultArtifactRef: ...


@dataclass(frozen=True)
class AgentToolContextServices:
    """创建工具执行上下文所需的稳定服务集合。"""

    result_artifact_service: ResultArtifactWriter
    result_store: ResultStore | None = None


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
    result_store: ResultStore | None = None
    config: Any = None
    # Run 创建时固定的时间上下文，时间工具只能读取，不能重新生成。
    temporal_context: TemporalContext | None = None
    # 检索 ACL 请求侧身份（P0-7）：角色解析在 P2-4 资产级授权落地前由调用方注入，
    # 检索 SQL 端的可见性谓词始终生效，字段缺失只表示无额外授权。
    principal_roles: list[str] = field(default_factory=list)
    principal_role_ids: list[int] = field(default_factory=list)
    permission_version: str | None = None
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

    @property
    def semantic_default_limit(self) -> int:
        return int(getattr(self.config, "default_limit", 100) or 100)

    @property
    def semantic_retrieval_request(self) -> RetrievalRequest | None:
        """把 ChatBI 已确认问题投影为公共检索请求。"""

        understanding = self.state.get("question_understanding")
        if not isinstance(understanding, dict):
            return None
        rewritten_question = understanding.get("rewritten_question")
        intent = understanding.get("intent")
        dataset_id = self.dataset_id or self.state.get("dataset_id")
        if (
            not isinstance(rewritten_question, str)
            or not rewritten_question.strip()
            or not isinstance(intent, dict)
            or not isinstance(dataset_id, int)
            or dataset_id <= 0
            or self.user_id is None
            or self.user_id <= 0
        ):
            return None
        return build_semantic_binding_request(
            request_id=self.execution_id,
            tenant_id=self.oid,
            actor_id=self.user_id,
            dataset_id=dataset_id,
            original_question=str(
                self.state.get("original_question")
                or self.state.get("question")
                or rewritten_question
            ),
            rewritten_question=rewritten_question,
            intent=intent,
            principal_roles=self.principal_roles or None,
            principal_role_ids=self.principal_role_ids or None,
            permission_version=self.permission_version,
        )

    @property
    def semantic_asset_scope(self) -> SemanticAssetScope | None:
        """读取由公共检索 Tool 结果处理器保存的可信编译范围。"""

        value = self.state.get("semantic_scope")
        if value is None:
            return None
        if isinstance(value, SemanticAssetScope):
            return value
        return SemanticAssetScope.model_validate(value)


class AgentTool(Tool[AgentToolContext, Any, Any]):
    """ChatBI 领域工具基类；继承通用 Tool 协议。"""


__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolContextServices",
]
