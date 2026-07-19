"""统一索引协调器和 worker 共用的物理 embedding profile。"""

from apps.retrieval.indexing import IndexEmbeddingProfile
from common.core.config import settings


def build_retrieval_index_profile() -> IndexEmbeddingProfile:
    """构造当前统一索引唯一允许使用的 embedding profile。"""

    return IndexEmbeddingProfile(
        name="bge-m3-1024",
        provider=settings.RETRIEVAL_EMBEDDING_PROVIDER,
        model=settings.RETRIEVAL_EMBEDDING_MODEL,
        dimension=settings.RETRIEVAL_EMBEDDING_DIMENSION,
    )


__all__ = ["build_retrieval_index_profile"]
