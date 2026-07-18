"""Datasource 对 Access Control 提供的工作空间资源范围适配器。"""

from sqlmodel import Session, select

from apps.datasource.models.datasource import CoreDatasource
from common.core.cache_keys import CacheName, CacheNamespace
from common.core.db import engine
from common.core.sqlbot_cache import cache


class DatasourceWorkspaceResourceScopeReader:
    """只公开当前工作空间可引用的数据源 ID。"""

    # 复用现有缓存键；旧缓存装饰器尚未提供完整类型声明。
    @cache(
        namespace=str(CacheNamespace.AUTH_INFO),
        cacheName=str(CacheName.DS_ID_LIST),
        keyExpression="workspace_id",
    )  # type: ignore[untyped-decorator]
    async def list_resource_ids(self, workspace_id: int) -> list[int]:
        with Session(engine) as session:
            resource_ids = session.exec(
                select(CoreDatasource.id)
                .distinct()
                .where(CoreDatasource.oid == workspace_id)
            ).all()
        return [resource_id for resource_id in resource_ids if resource_id is not None]
