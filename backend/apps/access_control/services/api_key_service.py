"""API Key 管理 Service。"""

from collections.abc import Callable
from typing import Final

from apps.access_control.errors import (
    ApiKeyDisabledError,
    ApiKeyLimitExceededError,
    ApiKeyNotFoundError,
    ApiKeyOwnershipError,
)
from apps.access_control.models.dto import ApiKeyGridItem, ApiKeyRecord
from apps.access_control.repository import ApiKeyRepository
from apps.access_control.services.identity_workspace_service import (
    IdentityWorkspaceService,
)

MAX_API_KEYS_PER_USER: Final = 5


class ApiKeyService:
    def __init__(
        self,
        repository: ApiKeyRepository,
        identity_service: IdentityWorkspaceService,
        *,
        generate_access_key: Callable[[], str],
        generate_secret_key: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._identity_service = identity_service
        self._generate_access_key = generate_access_key
        self._generate_secret_key = generate_secret_key

    def list_api_keys(self, user_id: int) -> list[ApiKeyGridItem]:
        return [
            ApiKeyGridItem.model_validate(record.model_dump())
            for record in self._repository.list_api_keys(user_id)
        ]

    def get_api_key_by_access_key(self, access_key: str) -> ApiKeyRecord | None:
        return self._repository.get_api_key_by_access_key(access_key)

    @staticmethod
    def require_active_api_key(api_key: ApiKeyRecord | None) -> ApiKeyRecord:
        if api_key is None:
            raise ApiKeyNotFoundError()
        if not api_key.status:
            raise ApiKeyDisabledError(api_key.access_key)
        return api_key

    def create_api_key(self, user_id: int, create_time: int) -> ApiKeyRecord:
        self._identity_service.get_user(user_id)
        api_key = self._repository.create_api_key(
            user_id=user_id,
            access_key=self._generate_access_key(),
            secret_key=self._generate_secret_key(),
            create_time=create_time,
            limit=MAX_API_KEYS_PER_USER,
        )
        if api_key is None:
            raise ApiKeyLimitExceededError(MAX_API_KEYS_PER_USER)
        return api_key

    def update_status(
        self,
        *,
        user_id: int,
        api_key_id: int,
        status: bool,
    ) -> ApiKeyRecord:
        api_key = self._get_owned_api_key(user_id, api_key_id, "modify")
        if api_key.status == status:
            return api_key
        updated = self._repository.update_api_key_status(api_key_id, status)
        if updated is None:
            raise ApiKeyNotFoundError(api_key_id)
        return updated

    def delete_api_key(self, *, user_id: int, api_key_id: int) -> ApiKeyRecord:
        self._get_owned_api_key(user_id, api_key_id, "delete")
        deleted = self._repository.delete_api_key(api_key_id)
        if deleted is None:
            raise ApiKeyNotFoundError(api_key_id)
        return deleted

    def _get_owned_api_key(
        self,
        user_id: int,
        api_key_id: int,
        action: str,
    ) -> ApiKeyRecord:
        api_key = self._repository.get_api_key(api_key_id)
        if api_key is None:
            raise ApiKeyNotFoundError(api_key_id)
        if api_key.uid != user_id:
            raise ApiKeyOwnershipError(action)
        return api_key
