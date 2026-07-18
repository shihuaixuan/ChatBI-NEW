"""登录认证与 API Key 业务规则测试。"""

from unittest.mock import Mock

import pytest

from apps.access_control.errors import (
    ApiKeyDisabledError,
    ApiKeyLimitExceededError,
    ApiKeyOwnershipError,
    InvalidCredentialsError,
    LocalLoginRequiredError,
    UserInactiveError,
    UserWorkspaceRequiredError,
)
from apps.access_control.models.dto import ApiKeyRecord, BaseUserDTO, UserInfoDTO
from apps.access_control.services import ApiKeyService, AuthenticationService


def _base_user(
    *,
    user_id: int = 2,
    workspace_id: int = 7,
    status: int = 1,
    origin: int = 0,
) -> BaseUserDTO:
    return BaseUserDTO(
        id=user_id,
        account="member",
        oid=workspace_id,
        language="zh-CN",
        password="hashed-password",
        status=status,
        origin=origin,
        name="Member",
    )


def _user_info(
    *,
    workspace_id: int = 7,
    status: int = 1,
) -> UserInfoDTO:
    return UserInfoDTO(
        id=2,
        account="member",
        oid=workspace_id,
        name="Member",
        email="member@example.com",
        status=status,
        origin=0,
    )


def _api_key(
    *,
    user_id: int = 2,
    status: bool = True,
) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=11,
        uid=user_id,
        access_key="access-key",
        secret_key="secret-key",
        status=status,
        create_time=1,
    )


def test_local_login_rejects_invalid_credentials() -> None:
    identity_service = Mock()
    identity_service.authenticate.return_value = None
    service = AuthenticationService(identity_service)

    with pytest.raises(InvalidCredentialsError):
        service.authenticate_local("member", "wrong")


@pytest.mark.parametrize(
    ("user", "error_type"),
    [
        (_base_user(workspace_id=0), UserWorkspaceRequiredError),
        (_base_user(status=0), UserInactiveError),
        (_base_user(origin=1), LocalLoginRequiredError),
    ],
)
def test_local_login_enforces_user_access_state(
    user: BaseUserDTO,
    error_type: type[Exception],
) -> None:
    identity_service = Mock()
    identity_service.authenticate.return_value = user
    service = AuthenticationService(identity_service)

    with pytest.raises(error_type):
        service.authenticate_local("member", "password")


def test_token_user_must_be_active_and_have_workspace() -> None:
    identity_service = Mock()
    service = AuthenticationService(identity_service)

    identity_service.get_user_info.return_value = _user_info(status=0)
    with pytest.raises(UserInactiveError):
        service.require_active_user(2)

    identity_service.get_user_info.return_value = _user_info(workspace_id=0)
    with pytest.raises(UserWorkspaceRequiredError):
        service.require_active_user(2)


def test_api_key_limit_is_reported_by_service() -> None:
    repository = Mock()
    repository.create_api_key.return_value = None
    identity_service = Mock()
    service = ApiKeyService(
        repository,
        identity_service,
        generate_access_key=lambda: "generated-access-key",
        generate_secret_key=lambda: "generated-secret-key",
    )

    with pytest.raises(ApiKeyLimitExceededError):
        service.create_api_key(2, 100)

    repository.create_api_key.assert_called_once_with(
        user_id=2,
        access_key="generated-access-key",
        secret_key="generated-secret-key",
        create_time=100,
        limit=5,
    )


def test_api_key_cannot_be_modified_by_another_user() -> None:
    repository = Mock()
    repository.get_api_key.return_value = _api_key(user_id=3)
    service = ApiKeyService(
        repository,
        Mock(),
        generate_access_key=lambda: "access",
        generate_secret_key=lambda: "secret",
    )

    with pytest.raises(ApiKeyOwnershipError):
        service.update_status(user_id=2, api_key_id=11, status=False)

    repository.update_api_key_status.assert_not_called()


def test_disabled_api_key_is_rejected_in_one_rule_entry() -> None:
    with pytest.raises(ApiKeyDisabledError):
        ApiKeyService.require_active_api_key(_api_key(status=False))
