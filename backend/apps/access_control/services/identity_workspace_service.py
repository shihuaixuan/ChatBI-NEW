"""用户、工作空间与成员关系业务 Service。"""

from collections.abc import Callable
from typing import Final

from apps.access_control.errors import (
    CurrentPasswordMismatchError,
    DefaultWorkspaceCannotDeleteError,
    InvalidUserEmailError,
    InvalidUserPasswordError,
    UnsupportedUserLanguageError,
    UnsupportedUserStatusError,
    UserAccountExistsError,
    UserAccountImmutableError,
    UserNotFoundError,
    WorkspaceMemberAlreadyExistsError,
    WorkspaceMemberNotFoundError,
    WorkspaceMembershipRequiredError,
    WorkspaceNotFoundError,
)
from apps.access_control.models.dto import (
    EMAIL_REGEX,
    PWD_REGEX,
    BaseUserDTO,
    UserCreator,
    UserEditor,
    UserGrid,
    UserInfoDTO,
    UserRecord,
    UserVariableAssignment,
    UserWs,
    UserWsOption,
    WorkspaceBase,
    WorkspaceEditor,
    WorkspaceRecord,
    WorkspaceUser,
)
from apps.access_control.repository import IdentityWorkspaceRepository
from common.core.schemas import PaginatedResponse

SYSTEM_ADMIN_USER_ID: Final = 1
DEFAULT_WORKSPACE_ID: Final = 1
SUPPORTED_LANGUAGES: Final = frozenset({"zh-CN", "zh-TW", "en", "ko-KR"})
SUPPORTED_USER_STATUSES: Final = frozenset({0, 1})


class IdentityWorkspaceService:
    """集中维护身份、成员集合与当前工作空间的一致性。"""

    def __init__(
        self,
        repository: IdentityWorkspaceRepository,
        *,
        verify_password: Callable[[str, str], bool],
        hash_password: Callable[[str], str],
        default_password: Callable[[], str],
        normalize_variable_assignments: Callable[
            [list[UserVariableAssignment] | None],
            list[UserVariableAssignment],
        ],
    ) -> None:
        self._repository = repository
        self._verify_password = verify_password
        self._hash_password = hash_password
        self._default_password = default_password
        self._normalize_variable_assignments = normalize_variable_assignments

    def get_user(self, user_id: int) -> UserRecord:
        user = self._repository.get_user(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def get_user_by_account(self, account: str) -> BaseUserDTO | None:
        user = self._repository.get_user_by_account(account)
        return BaseUserDTO.model_validate(user.model_dump()) if user else None

    def account_exists(self, account: str) -> bool:
        return self._repository.account_exists(account)

    def email_exists(self, email: str) -> bool:
        return self._repository.email_exists(email)

    def get_user_info(self, user_id: int) -> UserInfoDTO | None:
        user = self._repository.get_user(user_id)
        if user is None:
            return None
        user_info = UserInfoDTO.model_validate(user.model_dump())
        user_info.isAdmin = user.id == SYSTEM_ADMIN_USER_ID and user.account == "admin"
        if not user_info.isAdmin:
            weight = self._repository.get_membership_weight(user.id, user.oid)
            user_info.weight = weight if weight is not None else -1
        return user_info

    def authenticate(self, account: str, password: str) -> BaseUserDTO | None:
        user = self._repository.get_user_by_account(account)
        if user is None or not self._verify_password(password, user.password):
            return None
        return BaseUserDTO.model_validate(user.model_dump())

    def list_user_workspaces(self, user_id: int) -> list[UserWs]:
        self.get_user(user_id)
        return self._repository.list_user_workspaces(user_id)

    async def list_users(
        self,
        *,
        page: int,
        size: int,
        keyword: str | None,
        status: int | None,
        origins: list[int] | None,
        workspace_ids: list[int] | None,
    ) -> PaginatedResponse[UserGrid]:
        return await self._repository.list_users(
            page=page,
            size=size,
            keyword=keyword,
            status=status,
            origins=origins,
            workspace_ids=workspace_ids,
        )

    def get_user_detail(self, user_id: int) -> UserEditor:
        user = self.get_user(user_id)
        detail = UserEditor.model_validate(user.model_dump())
        detail.oid_list = [
            item.id for item in self._repository.list_user_workspaces(user_id)
        ]
        return detail

    def create_user(self, creator: UserCreator) -> UserRecord:
        if self._repository.account_exists(creator.account):
            raise UserAccountExistsError(creator.account)
        self._validate_email(creator.email)
        workspace_ids = list(dict.fromkeys(creator.oid_list or []))
        self._ensure_workspaces_exist(workspace_ids)
        # 历史接口允许 origin 传 null，持久化前统一为本地来源。
        normalized_creator = (
            creator
            if creator.origin is not None
            else creator.model_copy(update={"origin": 0})
        )
        normalized_creator = normalized_creator.model_copy(
            update={
                "system_variables": self._normalize_variable_assignments(
                    normalized_creator.system_variables
                )
            }
        )
        return self._repository.create_user(normalized_creator, workspace_ids)

    def update_user(self, editor: UserEditor) -> UserRecord:
        user = self.get_user(editor.id)
        if editor.account != user.account:
            raise UserAccountImmutableError(user.account)
        self._validate_email(editor.email)
        workspace_ids = list(dict.fromkeys(editor.oid_list or []))
        self._ensure_workspaces_exist(workspace_ids)
        normalized_editor = (
            editor
            if editor.origin is not None
            else editor.model_copy(update={"origin": 0})
        )
        if "system_variables" in editor.model_fields_set:
            normalized_editor = normalized_editor.model_copy(
                update={
                    "system_variables": self._normalize_variable_assignments(
                        normalized_editor.system_variables
                    )
                }
            )
        updated = self._repository.update_user(normalized_editor, workspace_ids)
        if updated is None:
            raise UserNotFoundError(editor.id)
        return updated

    def delete_users(self, user_ids: list[int]) -> list[int]:
        unique_user_ids = list(dict.fromkeys(user_ids))
        missing_ids = self._repository.find_missing_user_ids(set(unique_user_ids))
        if missing_ids:
            raise UserNotFoundError(min(missing_ids))
        return self._repository.delete_users(unique_user_ids)

    def switch_workspace(self, user_id: int, workspace_id: int) -> None:
        self.get_user(user_id)
        workspace = self._repository.get_workspace(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        if (
            user_id != SYSTEM_ADMIN_USER_ID
            and self._repository.get_membership_weight(user_id, workspace_id) is None
        ):
            raise WorkspaceMembershipRequiredError(workspace_id, workspace.name)
        self._repository.set_current_workspace(user_id, workspace_id)

    def update_language(self, user_id: int, language: str) -> UserRecord:
        if language not in SUPPORTED_LANGUAGES:
            raise UnsupportedUserLanguageError(language)
        user = self._repository.update_user_language(user_id, language)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def reset_password(self, user_id: int) -> UserRecord:
        user = self._repository.update_user_password(
            user_id,
            self._default_password(),
        )
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def update_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> UserRecord:
        if PWD_REGEX.fullmatch(new_password) is None:
            raise InvalidUserPasswordError()
        user = self.get_user(user_id)
        if not self._verify_password(current_password, user.password):
            raise CurrentPasswordMismatchError()
        updated = self._repository.update_user_password(
            user_id,
            self._hash_password(new_password),
        )
        if updated is None:
            raise UserNotFoundError(user_id)
        return updated

    def update_status(self, user_id: int, status: int) -> UserRecord:
        if status not in SUPPORTED_USER_STATUSES:
            raise UnsupportedUserStatusError(status)
        user = self._repository.update_user_status(user_id, status)
        if user is None:
            raise UserNotFoundError(user_id)
        return user

    def list_workspaces(self) -> list[WorkspaceRecord]:
        return self._repository.list_workspaces()

    def get_workspace(self, workspace_id: int) -> WorkspaceRecord:
        workspace = self._repository.get_workspace(workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        return workspace

    def create_workspace(
        self,
        creator: WorkspaceBase,
        create_time: int,
    ) -> WorkspaceRecord:
        return self._repository.create_workspace(creator.name, create_time)

    def update_workspace(self, editor: WorkspaceEditor) -> WorkspaceRecord:
        workspace = self._repository.update_workspace(editor.id, editor.name)
        if workspace is None:
            raise WorkspaceNotFoundError(editor.id)
        return workspace

    def delete_workspace(self, workspace_id: int) -> list[int]:
        if workspace_id == DEFAULT_WORKSPACE_ID:
            raise DefaultWorkspaceCannotDeleteError()
        affected_user_ids = self._repository.delete_workspace(
            workspace_id,
            DEFAULT_WORKSPACE_ID,
        )
        if affected_user_ids is None:
            raise WorkspaceNotFoundError(workspace_id)
        return affected_user_ids

    async def list_available_users(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[UserWsOption]:
        self.get_workspace(workspace_id)
        return await self._repository.list_available_users(
            workspace_id=workspace_id,
            page=page,
            size=size,
            keyword=keyword,
        )

    def find_available_user(
        self,
        *,
        workspace_id: int,
        keyword: str,
    ) -> UserWsOption | None:
        self.get_workspace(workspace_id)
        return self._repository.find_available_user(
            workspace_id=workspace_id,
            keyword=keyword,
        )

    async def list_workspace_members(
        self,
        *,
        workspace_id: int,
        page: int,
        size: int,
        keyword: str | None,
    ) -> PaginatedResponse[WorkspaceUser]:
        self.get_workspace(workspace_id)
        return await self._repository.list_workspace_members(
            workspace_id=workspace_id,
            page=page,
            size=size,
            keyword=keyword,
        )

    def bind_members(
        self,
        *,
        workspace_id: int,
        user_ids: list[int],
        weight: int,
    ) -> None:
        self.get_workspace(workspace_id)
        unique_user_ids = list(dict.fromkeys(user_ids))
        self._ensure_users_exist(unique_user_ids)
        existing_ids = self._repository.find_existing_member_ids(
            workspace_id,
            set(unique_user_ids),
        )
        if existing_ids:
            raise WorkspaceMemberAlreadyExistsError(workspace_id, min(existing_ids))
        self._repository.bind_members(workspace_id, unique_user_ids, weight)

    def update_member_weight(
        self,
        *,
        workspace_id: int,
        user_id: int,
        weight: int,
    ) -> None:
        if not self._repository.update_member_weight(workspace_id, user_id, weight):
            raise WorkspaceMemberNotFoundError(workspace_id)

    def unbind_members(
        self,
        *,
        workspace_id: int,
        user_ids: list[int],
    ) -> list[int]:
        found_user_ids = self._repository.unbind_members(
            workspace_id,
            list(dict.fromkeys(user_ids)),
        )
        if not found_user_ids:
            raise WorkspaceMemberNotFoundError(workspace_id)
        return found_user_ids

    @staticmethod
    def _validate_email(email: str) -> None:
        if EMAIL_REGEX.fullmatch(email) is None:
            raise InvalidUserEmailError(email)

    def _ensure_users_exist(self, user_ids: list[int]) -> None:
        missing_ids = self._repository.find_missing_user_ids(set(user_ids))
        if missing_ids:
            raise UserNotFoundError(min(missing_ids))

    def _ensure_workspaces_exist(self, workspace_ids: list[int]) -> None:
        missing_ids = self._repository.find_missing_workspace_ids(set(workspace_ids))
        if missing_ids:
            raise WorkspaceNotFoundError(min(missing_ids))
