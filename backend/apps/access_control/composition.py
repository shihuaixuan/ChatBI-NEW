"""Access Control 依赖组装入口。"""

from functools import lru_cache

from apps.access_control.repository import CompositeWorkspaceResourceScopeRepository
from apps.access_control.services import AuthorizationService
from apps.chat.resource_scope import ChatWorkspaceResourceScopeReader
from apps.datasource.resource_scope import DatasourceWorkspaceResourceScopeReader


@lru_cache(maxsize=1)
def build_authorization_service() -> AuthorizationService:
    datasource_reader = DatasourceWorkspaceResourceScopeReader()
    return AuthorizationService(
        resource_scope_repository=CompositeWorkspaceResourceScopeRepository(
            readers={
                "chat": ChatWorkspaceResourceScopeReader(),
                "ds": datasource_reader,
                "datasource": datasource_reader,
            }
        )
    )

