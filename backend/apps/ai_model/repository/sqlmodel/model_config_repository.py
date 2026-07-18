"""AI 模型运行时配置的 SQLModel 仓储实现。"""

from sqlmodel import Session, col, select

from apps.ai_model.models.dto import StoredAIModelConfig
from apps.ai_model.models.orm import AiModelDetail
from apps.ai_model.repository.model_config_repository import AIModelConfigRepository


class SQLModelAIModelConfigRepository(AIModelConfigRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, model_id: int) -> StoredAIModelConfig | None:
        model = self._session.get(AiModelDetail, model_id)
        return self._to_snapshot(model)

    def get_default(self) -> StoredAIModelConfig | None:
        model = self._session.exec(
            select(AiModelDetail).where(col(AiModelDetail.default_model).is_(True))
        ).first()
        return self._to_snapshot(model)

    @staticmethod
    def _to_snapshot(model: AiModelDetail | None) -> StoredAIModelConfig | None:
        if model is None:
            return None
        if model.id is None:
            raise RuntimeError("AI_MODEL_ID_MISSING")
        return StoredAIModelConfig(
            model_id=model.id,
            protocol=model.protocol,
            base_model=model.base_model,
            api_key=model.api_key,
            api_domain=model.api_domain,
            config=model.config,
        )
