"""用户登录与访问状态校验 Service。"""

from apps.access_control.errors import (
    InvalidCredentialsError,
    LocalLoginRequiredError,
    UserInactiveError,
    UserNotFoundError,
    UserWorkspaceRequiredError,
)
from apps.access_control.models.dto import BaseUserDTO, UserInfoDTO
from apps.access_control.services.identity_workspace_service import (
    IdentityWorkspaceService,
)


class AuthenticationService:
    def __init__(self, identity_service: IdentityWorkspaceService) -> None:
        self._identity_service = identity_service

    def authenticate_local(self, account: str, password: str) -> BaseUserDTO:
        user = self._identity_service.authenticate(account, password)
        if user is None:
            raise InvalidCredentialsError()
        if not user.oid:
            raise UserWorkspaceRequiredError(user.id)
        if user.status != 1:
            raise UserInactiveError(user.id)
        if user.origin != 0:
            raise LocalLoginRequiredError(user.id)
        return user

    def require_active_user(self, user_id: int) -> UserInfoDTO:
        user = self._identity_service.get_user_info(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.status != 1:
            raise UserInactiveError(user.id)
        if not user.oid:
            raise UserWorkspaceRequiredError(user.id)
        return user
