"""用户、工作空间与成员关系仓储端口。"""

from typing import Protocol

from apps.access_control.models.dto import (
    UserCreator,
    UserEditor,
    UserGrid,
    UserRecord,
    UserWs,
    UserWsOption,
    WorkspaceRecord,
    WorkspaceUser,
)
from common.core.schemas import PaginatedResponse


class IdentityWorkspaceRepository(Protocol):
    def get_user(self, user_id: int) -> UserRecord | None: ...

    def get_user_by_account(self, account: str) -> UserRecord | None: ...

    def account_exists(self, account: str) -> bool: ...

    def email_exists(self, email: str) -> bool: ...

    def find_missing_user_ids(self, user_ids: set[int]) -> set[int]: ...

    def find_missing_workspace_ids(self, workspace_ids: set[int]) -> set[int]: ...

    def get_membership_weight(self, user_id: int, workspace_id: int) -> int | None: ...

    def list_user_workspaces(self, user_id: int) -> list[UserWs]: ...

    async def list_users(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
        status: int | None,
        origins: list[int] | None,
        workspace_ids: list[int] | None,
    ) -> PaginatedResponse[UserGrid]: ...

    def create_user(
        self,
        creator: UserCreator,
        workspace_ids: list[int],
    ) -> UserRecord: ...

    def update_user(
        self,
        editor: UserEditor,
        workspace_ids: list[int],
    ) -> UserRecord | None: ...

    def delete_users(self, user_ids: list[int]) -> list[int]: ...

    def set_current_workspace(self, user_id: int, workspace_id: int) -> None: ...

    def update_user_language(self, user_id: int, language: str) -> UserRecord | None: ...

    def update_user_password(self, user_id: int, password: str) -> UserRecord | None: ...

    def update_user_status(self, user_id: int, status: int) -> UserRecord | None: ...

    def get_workspace(self, workspace_id: int) -> WorkspaceRecord | None: ...

    def list_workspaces(self) -> list[WorkspaceRecord]: ...

    def create_workspace(self, name: str, create_time: int) -> WorkspaceRecord: ...

    def update_workspace(
        self,
        workspace_id: int,
        name: str,
    ) -> WorkspaceRecord | None: ...

    def delete_workspace(
        self,
        workspace_id: int,
        default_workspace_id: int,
    ) -> list[int] | None: ...

    async def list_available_users(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[UserWsOption]: ...

    def find_available_user(
        self,
        *,
        workspace_id: int,
        keyword: str,
    ) -> UserWsOption | None: ...

    async def list_workspace_members(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[WorkspaceUser]: ...

    def find_existing_member_ids(
        self,
        workspace_id: int,
        user_ids: set[int],
    ) -> set[int]: ...

    def bind_members(
        self,
        workspace_id: int,
        user_ids: list[int],
        weight: int,
    ) -> None: ...

    def update_member_weight(
        self,
        workspace_id: int,
        user_id: int,
        weight: int,
    ) -> bool: ...

    def unbind_members(
        self,
        workspace_id: int,
        user_ids: list[int],
    ) -> list[int]: ...
