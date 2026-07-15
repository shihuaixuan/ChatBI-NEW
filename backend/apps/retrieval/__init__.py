"""ChatBI 统一检索领域。

本包只表达检索请求、结果、策略与评测能力，不依赖 Graph、Agent 或 API 运行时。
"""

from apps.retrieval.hybrid import HybridRecallResult, SemanticBindingHybridRetriever
from apps.retrieval.indexing import IndexEmbeddingProfile, RetrievalIndexingService
from apps.retrieval.planner import RetrievalQueryPlan, SemanticBindingQueryPlanner
from apps.retrieval.schemas import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalProfileName,
    RetrievalRequest,
)
from apps.retrieval.service import (
    RetrievalService,
    RetrievalServiceResult,
    build_semantic_binding_shadow_request,
)

__all__ = [
    "AssetReference",
    "ExecutableAssetReference",
    "HybridRecallResult",
    "IndexEmbeddingProfile",
    "RetrievalBundle",
    "RetrievalDecisionStatus",
    "RetrievalProfileName",
    "RetrievalRequest",
    "RetrievalIndexingService",
    "RetrievalQueryPlan",
    "RetrievalService",
    "RetrievalServiceResult",
    "SemanticBindingHybridRetriever",
    "SemanticBindingQueryPlanner",
    "build_semantic_binding_shadow_request",
]
