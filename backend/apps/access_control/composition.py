"""Access Control 依赖组装入口。"""

import secrets
from functools import lru_cache

from sqlmodel import Session

from apps.access_control.repository import CompositeWorkspaceResourceScopeRepository
from apps.access_control.repository.sqlmodel import (
    SQLModelAccessVariableRepository,
    SQLModelApiKeyRepository,
    SQLModelDataPolicyRepository,
    SQLModelIdentityWorkspaceRepository,
)
from apps.access_control.services import (
    AccessVariableService,
    ApiKeyService,
    AuthenticationService,
    AuthorizationService,
    DataPolicyService,
    IdentityWorkspaceService,
)
from apps.chatbi import ChatWorkspaceResourceScopeReader
from apps.datasource import build_datasource_policy_catalog
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
    variable_service = build_access_variable_service(session)
    return IdentityWorkspaceService(
        SQLModelIdentityWorkspaceRepository(session),
        verify_password=verify_md5pwd,
        hash_password=md5pwd,
        default_password=default_md5_pwd,
        normalize_variable_assignments=variable_service.normalize_assignments,
    )


def build_access_variable_service(session: Session) -> AccessVariableService:
    return AccessVariableService(SQLModelAccessVariableRepository(session))


def build_data_policy_service(session: Session) -> DataPolicyService:
    return DataPolicyService(
        SQLModelDataPolicyRepository(session),
        build_datasource_policy_catalog(session),
        build_access_variable_service(session),
    )


def build_authentication_service(session: Session) -> AuthenticationService:
    return AuthenticationService(build_identity_workspace_service(session))


def build_api_key_service(session: Session) -> ApiKeyService:
    return ApiKeyService(
        SQLModelApiKeyRepository(session),
        build_identity_workspace_service(session),
        generate_access_key=lambda: secrets.token_urlsafe(16),
        generate_secret_key=lambda: secrets.token_urlsafe(32),
    )
