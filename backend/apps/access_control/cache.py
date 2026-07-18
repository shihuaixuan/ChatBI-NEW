"""Access Control 身份缓存入口。"""

from common.core.sqlbot_cache import clear_cache
from common.utils.utils import SQLBotLogUtil

AUTH_CACHE_NAMESPACE = "sqlbot:auth"
USER_INFO_CACHE_NAME = "user:info"


@clear_cache(
    namespace=AUTH_CACHE_NAMESPACE,
    cacheName=USER_INFO_CACHE_NAME,
    keyExpression="user_id",
)  # type: ignore[untyped-decorator]
async def clear_user_cache(user_id: int) -> None:
    SQLBotLogUtil.info(f"User cache for [{user_id}] has been cleaned")
