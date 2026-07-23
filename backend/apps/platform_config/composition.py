from sqlmodel import Session

from apps.platform_config.repository.sqlmodel import (
    SQLModelPlatformParameterRepository,
)
from apps.platform_config.services import PlatformParameterService


def build_platform_parameter_service(session: Session) -> PlatformParameterService:
    """装配平台参数公开查询服务。"""

    return PlatformParameterService(SQLModelPlatformParameterRepository(session))


__all__ = ["build_platform_parameter_service"]
