"""API Key 仓储端口。"""

from typing import Protocol

from apps.access_control.models.dto import ApiKeyRecord


class ApiKeyRepository(Protocol):
    def list_api_keys(self, user_id: int) -> list[ApiKeyRecord]: ...

    def get_api_key(self, api_key_id: int) -> ApiKeyRecord | None: ...

    def get_api_key_by_access_key(self, access_key: str) -> ApiKeyRecord | None: ...

    def create_api_key(
        self,
        *,
        user_id: int,
        access_key: str,
        secret_key: str,
        create_time: int,
        limit: int,
    ) -> ApiKeyRecord | None: ...

    def update_api_key_status(
        self,
        api_key_id: int,
        status: bool,
    ) -> ApiKeyRecord | None: ...

    def delete_api_key(self, api_key_id: int) -> ApiKeyRecord | None: ...
