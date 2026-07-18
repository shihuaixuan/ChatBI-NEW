"""旧 System 用户查询路径兼容层。"""

from sqlmodel import Session

from apps.access_control.cache import clear_user_cache
from apps.access_control.composition import build_identity_workspace_service
from apps.access_control.identity import (
    authenticate,
    get_user_by_account,
    get_user_info,
    user_ws_options,
)
from apps.access_control.models import UserModel
from apps.access_control.models.dto import EMAIL_REGEX, PWD_REGEX
from common.core.deps import SessionDep


def get_db_user(*, session: Session, user_id: int) -> UserModel | None:
    return session.get(UserModel, user_id)


async def single_delete(session: SessionDep, id: int) -> None:
    build_identity_workspace_service(session).delete_users([id])
    await clear_user_cache(id)


async def clean_user_cache(id: int) -> None:
    await clear_user_cache(id)


def check_account_exists(*, session: Session, account: str) -> bool:
    return build_identity_workspace_service(session).account_exists(account)


def check_email_exists(*, session: Session, email: str) -> bool:
    return build_identity_workspace_service(session).email_exists(email)


def check_email_format(email: str) -> bool:
    return EMAIL_REGEX.fullmatch(email) is not None


def check_pwd_format(password: str) -> bool:
    return PWD_REGEX.fullmatch(password) is not None


__all__ = [
    "authenticate",
    "check_account_exists",
    "check_email_exists",
    "check_email_format",
    "check_pwd_format",
    "clean_user_cache",
    "get_db_user",
    "get_user_by_account",
    "get_user_info",
    "single_delete",
    "user_ws_options",
]
