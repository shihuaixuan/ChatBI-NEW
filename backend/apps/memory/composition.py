"""用户记忆模块唯一组合入口。"""

from sqlmodel import Session

from apps.memory.repository.sqlmodel import SQLModelMemoryRepository
from apps.memory.services import MemoryEvaluationService, MemoryService
from apps.retrieval.embedding import default_retrieval_embedding_provider
from common.core.config import settings


def build_memory_service(session: Session) -> MemoryService:
    """装配用户记忆读写服务。"""

    embedding_provider = None
    if settings.CHATBI_MEMORY_EMBEDDING_ENABLED:
        embedding_provider = default_retrieval_embedding_provider()
    return MemoryService(
        SQLModelMemoryRepository(session),
        embedding_provider=embedding_provider,
        recall_experiment_enabled=settings.CHATBI_MEMORY_RECALL_EXPERIMENT_ENABLED,
        recall_experiment_treatment_percent=(
            settings.CHATBI_MEMORY_RECALL_TREATMENT_PERCENT
        ),
        recall_experiment_salt=settings.CHATBI_MEMORY_RECALL_EXPERIMENT_SALT,
        recall_min_evaluated_per_variant=(
            settings.CHATBI_MEMORY_RECALL_MIN_EVALUATED_PER_VARIANT
        ),
        recall_max_adoption_drop=settings.CHATBI_MEMORY_RECALL_MAX_ADOPTION_DROP,
    )


def build_memory_evaluation_service() -> MemoryEvaluationService:
    """装配无状态的用户记忆离线评测服务。"""

    return MemoryEvaluationService()


__all__ = ["build_memory_evaluation_service", "build_memory_service"]
