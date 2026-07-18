"""Access Control 身份缓存入口。"""

from sqlmodel import Session

from apps.access_control.composition import build_api_key_service
from apps.access_control.models.dto import ApiKeyRecord
from common.core.sqlbot_cache import cache, clear_cache
from common.utils.utils import SQLBotLogUtil

AUTH_CACHE_NAMESPACE = "sqlbot:auth"
USER_INFO_CACHE_NAME = "user:info"
API_KEY_CACHE_NAME = "ask:info"


@clear_cache(
    namespace=AUTH_CACHE_NAMESPACE,
    cacheName=USER_INFO_CACHE_NAME,
    keyExpression="user_id",
)  # type: ignore[untyped-decorator]
async def clear_user_cache(user_id: int) -> None:
    SQLBotLogUtil.info(f"User cache for [{user_id}] has been cleaned")


@cache(
    namespace=AUTH_CACHE_NAMESPACE,
    cacheName=API_KEY_CACHE_NAME,
    keyExpression="access_key",
)  # type: ignore[untyped-decorator]
async def get_api_key(
    session: Session,
    access_key: str,
) -> ApiKeyRecord | None:
    return build_api_key_service(session).get_api_key_by_access_key(access_key)


@clear_cache(
    namespace=AUTH_CACHE_NAMESPACE,
    cacheName=API_KEY_CACHE_NAME,
    keyExpression="access_key",
)  # type: ignore[untyped-decorator]
async def clear_api_key_cache(access_key: str) -> None:
    SQLBotLogUtil.info(f"Api key cache for [{access_key}] has been cleaned")
