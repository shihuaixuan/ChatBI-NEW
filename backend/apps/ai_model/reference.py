"""其他领域校验 AI 模型引用时使用的公开能力。"""

from sqlmodel import Session

from apps.ai_model.repository import AIModelConfigRepository
from apps.ai_model.repository.sqlmodel import SQLModelAIModelConfigRepository


class AIModelReferenceService:
    def __init__(self, repository: AIModelConfigRepository) -> None:
        self._repository = repository

    def exists(self, model_id: int) -> bool:
        return self._repository.get_by_id(model_id) is not None


def build_ai_model_reference_service(
    session: Session,
) -> AIModelReferenceService:
    return AIModelReferenceService(SQLModelAIModelConfigRepository(session))
