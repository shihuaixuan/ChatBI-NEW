"""ChatBI 统一检索领域。

本包只表达检索请求、结果、策略与评测能力，不依赖 Graph、Agent 或 API 运行时。
"""

from apps.retrieval.indexing.service import (
    IndexEmbeddingProfile,
    RetrievalIndexingService,
)
from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBundle,
    RetrievalDecisionStatus,
    RetrievalProfileName,
    RetrievalRequest,
)
from apps.retrieval.projection.planner import (
    RetrievalQueryPlan,
    SemanticBindingQueryPlanner,
)
from apps.retrieval.query.compilation import validate_compilation_assets
from apps.retrieval.query.hybrid import (
    HybridRecallResult,
    SemanticBindingHybridRetriever,
)
from apps.retrieval.query.policy import (
    SemanticBindingPolicy,
    SemanticBindingPolicyResult,
)
from apps.retrieval.query.service import (
    RetrievalService,
    RetrievalServiceResult,
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
    "SemanticBindingPolicy",
    "SemanticBindingPolicyResult",
    "SemanticBindingQueryPlanner",
    "validate_compilation_assets",
]
