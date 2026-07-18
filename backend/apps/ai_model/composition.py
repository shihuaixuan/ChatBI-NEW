"""AI Model 依赖组装入口。"""

from sqlmodel import Session

from apps.ai_model.models.dto import LLMConfig
from apps.ai_model.repository.sqlmodel import SQLModelAIModelConfigRepository
from apps.ai_model.services import AIModelRuntimeConfigService
from common.core.db import engine
from common.utils.crypto import sqlbot_decrypt


def build_ai_model_runtime_config_service(
    session: Session,
) -> AIModelRuntimeConfigService:
    return AIModelRuntimeConfigService(
        repository=SQLModelAIModelConfigRepository(session),
        decrypt_secret=sqlbot_decrypt,
    )


async def get_ai_model_runtime_config(
    model_id: int | None = None,
) -> LLMConfig:
    with Session(engine) as session:
        return await build_ai_model_runtime_config_service(session).resolve(model_id)
