"""供认证中间件和外部接口使用的身份查询入口。"""

from collections.abc import Callable

from sqlmodel import Session

from apps.access_control.cache import AUTH_CACHE_NAMESPACE, USER_INFO_CACHE_NAME
from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.models.dto import BaseUserDTO, UserInfoDTO, UserWs
from common.core.sqlbot_cache import cache


def get_user_by_account(
    *,
    session: Session,
    account: str,
) -> BaseUserDTO | None:
    return build_identity_workspace_service(session).get_user_by_account(account)


@cache(
    namespace=AUTH_CACHE_NAMESPACE,
    cacheName=USER_INFO_CACHE_NAME,
    keyExpression="user_id",
)  # type: ignore[untyped-decorator]
async def get_user_info(
    *,
    session: Session,
    user_id: int,
) -> UserInfoDTO | None:
    return build_identity_workspace_service(session).get_user_info(user_id)


def authenticate(
    *,
    session: Session,
    account: str,
    password: str,
) -> BaseUserDTO | None:
    return build_identity_workspace_service(session).authenticate(account, password)


async def user_ws_options(
    session: Session,
    user_id: int,
    trans: Callable[[str], str] | None = None,
) -> list[UserWs]:
    workspaces = build_identity_workspace_service(session).list_user_workspaces(user_id)
    if trans is None:
        return workspaces
    localized = [
        UserWs(
            id=workspace.id,
            name=trans(workspace.name)
            if workspace.name.startswith("i18n")
            else workspace.name,
        )
        for workspace in workspaces
    ]
    return sorted(localized, key=lambda workspace: workspace.name)
