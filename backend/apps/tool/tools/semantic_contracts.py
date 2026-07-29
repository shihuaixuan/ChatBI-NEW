"""Semantic 公共 Tool 的可信上下文与跨工具范围。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from apps.retrieval import (
    ExecutableAssetReference,
    RetrievalDecisionStatus,
    RetrievalRequest,
)
from apps.tool.tools.context import TrustedToolContext


class SemanticAssetScope(BaseModel):
    """语义检索成功后由服务端保存的 SQL 编译范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    datasource_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    retrieval_id: str = Field(min_length=1)
    decision_status: RetrievalDecisionStatus
    allowed_assets: tuple[ExecutableAssetReference, ...] = ()
    authorized_tables: tuple[str, ...] = ()
    normalized_time_range: dict[str, Any] | None = None
    permission_version: str | None = None


class SemanticToolContext(TrustedToolContext, Protocol):
    """Semantic Tool 可读取的服务端可信输入。"""

    @property
    def semantic_retrieval_request(self) -> RetrievalRequest | None: ...

    @property
    def semantic_asset_scope(self) -> SemanticAssetScope | None: ...

    @property
    def semantic_default_limit(self) -> int: ...


__all__ = ["SemanticAssetScope", "SemanticToolContext"]
