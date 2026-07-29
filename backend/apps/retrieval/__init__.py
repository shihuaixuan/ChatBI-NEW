"""ChatBI 统一检索领域。

本包只表达检索请求、结果、策略与评测能力，不依赖 Graph、Agent 或 API 运行时。
"""

from apps.retrieval.errors import (
    RetrievalConfigurationError,
    RetrievalPermissionError,
    RetrievalQueryError,
)
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
from apps.retrieval.projection.authorization import filter_semantic_payload_tables
from apps.retrieval.projection.planner import (
    RetrievalQueryPlan,
    SemanticBindingQueryPlanner,
)
from apps.retrieval.query.compilation import (
    validate_compilation_allowlist,
    validate_compilation_assets,
)
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
    build_semantic_binding_request,
)

__all__ = [
    "AssetReference",
    "ExecutableAssetReference",
    "HybridRecallResult",
    "IndexEmbeddingProfile",
    "RetrievalBundle",
    "RetrievalDecisionStatus",
    "RetrievalConfigurationError",
    "RetrievalProfileName",
    "RetrievalRequest",
    "RetrievalIndexingService",
    "RetrievalQueryPlan",
    "RetrievalService",
    "RetrievalServiceResult",
    "RetrievalPermissionError",
    "RetrievalQueryError",
    "SemanticBindingHybridRetriever",
    "SemanticBindingPolicy",
    "SemanticBindingPolicyResult",
    "SemanticBindingQueryPlanner",
    "validate_compilation_assets",
    "validate_compilation_allowlist",
    "build_semantic_binding_request",
    "filter_semantic_payload_tables",
]
