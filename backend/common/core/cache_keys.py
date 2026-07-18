"""跨领域缓存命名空间和键名称。"""

from enum import Enum


class CacheNamespace(Enum):
    AUTH_INFO = "sqlbot:auth"
    EMBEDDED_INFO = "sqlbot:embedded"

    def __str__(self) -> str:
        return self.value


class CacheName(Enum):
    USER_INFO = "user:info"
    ASSISTANT_INFO = "assistant:info"
    ASSISTANT_DS = "assistant:ds"
    ASK_INFO = "ask:info"
    DS_ID_LIST = "ds:id:list"

    def __str__(self) -> str:
        return self.value
