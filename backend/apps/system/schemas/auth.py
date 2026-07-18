from pydantic import BaseModel

from common.core.cache_keys import CacheName as CacheName
from common.core.cache_keys import CacheNamespace as CacheNamespace


class LocalLoginSchema(BaseModel):
    account: str
    password: str
