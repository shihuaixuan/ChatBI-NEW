"""Access Control 领域错误到现有 HTTP 契约的转换。"""

from collections.abc import Callable
from typing import NoReturn

from fastapi import HTTPException

from apps.access_control.errors import (
    ApiKeyLimitExceededError,
    ApiKeyNotFoundError,
    ApiKeyOwnershipError,
    CurrentPasswordMismatchError,
    DefaultWorkspaceCannotDeleteError,
    InvalidCredentialsError,
    InvalidUserEmailError,
    InvalidUserPasswordError,
    LocalLoginRequiredError,
    UnsupportedUserLanguageError,
    UnsupportedUserStatusError,
    UserAccountExistsError,
    UserAccountImmutableError,
    UserInactiveError,
    UserNotFoundError,
    UserWorkspaceRequiredError,
    WorkspaceMemberAlreadyExistsError,
    WorkspaceMemberNotFoundError,
    WorkspaceMembershipRequiredError,
    WorkspaceNotFoundError,
)


def raise_login_http_error(
    exc: Exception,
    trans: Callable[..., str],
) -> NoReturn:
    if isinstance(exc, InvalidCredentialsError):
        raise HTTPException(
            status_code=400,
            detail=trans("i18n_login.account_pwd_error"),
        ) from exc
    if isinstance(exc, UserWorkspaceRequiredError):
        raise HTTPException(
            status_code=400,
            detail=trans(
                "i18n_login.no_associated_ws",
                msg=trans("i18n_concat_admin"),
            ),
        ) from exc
    if isinstance(exc, UserInactiveError):
        raise HTTPException(
            status_code=400,
            detail=trans(
                "i18n_login.user_disable",
                msg=trans("i18n_concat_admin"),
            ),
        ) from exc
    if isinstance(exc, LocalLoginRequiredError):
        raise HTTPException(
            status_code=400,
            detail=trans("i18n_login.origin_error"),
        ) from exc
    raise exc


def raise_api_key_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ApiKeyLimitExceededError):
        raise ValueError(f"Maximum of {exc.limit} API keys allowed") from exc
    if isinstance(exc, ApiKeyNotFoundError):
        raise ValueError("API Key not found") from exc
    if isinstance(exc, ApiKeyOwnershipError):
        if exc.action == "modify":
            raise PermissionError("No permission to modify this API Key") from exc
        raise PermissionError("No permission to delete this API Key") from exc
    raise exc


def raise_identity_http_error(
    exc: Exception,
    trans: Callable[..., str],
) -> NoReturn:
    if isinstance(exc, UserAccountExistsError):
        raise Exception(
            trans(
                "i18n_exist",
                msg=f"{trans('i18n_user.account')} [{exc.account}]",
            )
        ) from exc
    if isinstance(exc, InvalidUserEmailError):
        raise Exception(
            trans(
                "i18n_format_invalid",
                key=f"{trans('i18n_user.email')} [{exc.email}]",
            )
        ) from exc
    if isinstance(exc, UserAccountImmutableError):
        raise Exception("account cannot be changed!") from exc
    if isinstance(exc, UserNotFoundError):
        raise HTTPException(
            status_code=404,
            detail=f"User with id [{exc.user_id}] not found!",
        ) from exc
    if isinstance(exc, WorkspaceMembershipRequiredError):
        raise Exception(
            trans("i18n_user.ws_miss", ws=exc.workspace_name)
        ) from exc
    if isinstance(exc, WorkspaceNotFoundError):
        raise HTTPException(
            status_code=404,
            detail=f"WorkspaceModel with id {exc.workspace_id} not found",
        ) from exc
    if isinstance(exc, DefaultWorkspaceCannotDeleteError):
        raise HTTPException(
            status_code=400,
            detail="Can not delete default workspace",
        ) from exc
    if isinstance(exc, WorkspaceMemberNotFoundError):
        raise HTTPException(status_code=404, detail="UserWsModel not found") from exc
    if isinstance(exc, WorkspaceMemberAlreadyExistsError):
        raise HTTPException(
            status_code=409,
            detail=f"UserWsModel already exists for user {exc.user_id}",
        ) from exc
    if isinstance(exc, UnsupportedUserLanguageError):
        raise Exception(
            trans("i18n_user.language_not_support", key=exc.language)
        ) from exc
    if isinstance(exc, InvalidUserPasswordError):
        raise Exception(
            trans("i18n_format_invalid", key=trans("i18n_user.password"))
        ) from exc
    if isinstance(exc, CurrentPasswordMismatchError):
        raise Exception(trans("i18n_error", key=trans("i18n_user.password"))) from exc
    if isinstance(exc, UnsupportedUserStatusError):
        raise HTTPException(status_code=422, detail="status not supported") from exc
    raise exc
