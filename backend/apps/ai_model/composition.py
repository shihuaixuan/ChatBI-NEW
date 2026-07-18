"""AI Model 依赖组装入口。"""

from sqlmodel import Session

from apps.ai_model.models.dto import LLMConfig
from apps.ai_model.repository.sqlmodel import (
    SQLModelAIModelConfigRepository,
    SQLModelAIModelManagementRepository,
)
from apps.ai_model.services import (
    AIModelManagementService,
    AIModelRuntimeConfigService,
    AIModelSecretMigrationService,
)
from common.core.db import engine
from common.utils.crypto import sqlbot_decrypt, sqlbot_encrypt
from common.utils.utils import SQLBotLogUtil


def build_ai_model_runtime_config_service(
    session: Session,
) -> AIModelRuntimeConfigService:
    return AIModelRuntimeConfigService(
        repository=SQLModelAIModelConfigRepository(session),
        decrypt_secret=sqlbot_decrypt,
    )


def build_ai_model_management_service(
    session: Session,
) -> AIModelManagementService:
    return AIModelManagementService(
        repository=SQLModelAIModelManagementRepository(session),
        decrypt_secret=sqlbot_decrypt,
    )


async def get_ai_model_runtime_config(
    model_id: int | None = None,
) -> LLMConfig:
    with Session(engine) as session:
        return await build_ai_model_runtime_config_service(session).resolve(model_id)


async def migrate_ai_model_secrets() -> int:
    with Session(engine) as session:
        migrated_count = await AIModelSecretMigrationService(
            repository=SQLModelAIModelManagementRepository(session),
            encrypt_secret=sqlbot_encrypt,
        ).migrate()
    if migrated_count:
        SQLBotLogUtil.info(
            f"AI 模型历史密钥迁移完成，共更新 {migrated_count} 条配置"
        )
    return migrated_count
