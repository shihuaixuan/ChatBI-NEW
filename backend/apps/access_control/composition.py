"""Access Control 依赖组装入口。"""

from functools import lru_cache

from sqlmodel import Session

from apps.access_control.repository import CompositeWorkspaceResourceScopeRepository
from apps.access_control.repository.sqlmodel import SQLModelIdentityWorkspaceRepository
from apps.access_control.services import AuthorizationService, IdentityWorkspaceService
from apps.chat.resource_scope import ChatWorkspaceResourceScopeReader
from apps.datasource.resource_scope import DatasourceWorkspaceResourceScopeReader
from common.core.security import default_md5_pwd, md5pwd, verify_md5pwd


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


def build_identity_workspace_service(session: Session) -> IdentityWorkspaceService:
    return IdentityWorkspaceService(
        SQLModelIdentityWorkspaceRepository(session),
        verify_password=verify_md5pwd,
        hash_password=md5pwd,
        default_password=default_md5_pwd,
    )
