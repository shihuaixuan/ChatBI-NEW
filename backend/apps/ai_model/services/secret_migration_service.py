"""AI 模型存量密钥迁移服务。"""

from collections.abc import Awaitable, Callable

from apps.ai_model.models.dto import AIModelSecretUpdate
from apps.ai_model.repository import AIModelManagementRepository

SecretEncryptor = Callable[[str], Awaitable[str]]


class AIModelSecretMigrationService:
    def __init__(
        self,
        repository: AIModelManagementRepository,
        encrypt_secret: SecretEncryptor,
    ) -> None:
        self._repository = repository
        self._encrypt_secret = encrypt_secret

    async def migrate(self) -> int:
        """加密历史明文密钥并修正旧供应商编号，未知错误直接向上抛出。"""

        updates: list[AIModelSecretUpdate] = []
        for model in self._repository.list_models():
            api_domain = model.api_domain
            api_key = model.api_key
            supplier = 15 if model.supplier == 12 else model.supplier
            if api_domain.startswith("http"):
                api_domain = await self._encrypt_secret(api_domain)
                if api_key:
                    api_key = await self._encrypt_secret(api_key)
            if (
                api_domain != model.api_domain
                or api_key != model.api_key
                or supplier != model.supplier
            ):
                updates.append(
                    AIModelSecretUpdate(
                        model_id=model.id,
                        api_domain=api_domain,
                        api_key=api_key,
                        supplier=supplier,
                    )
                )
        self._repository.apply_secret_updates(updates)
        return len(updates)
