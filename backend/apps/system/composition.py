from sqlmodel import Session

from apps.system.repository.sqlmodel import SQLModelSystemParameterRepository
from apps.system.services import SystemParameterService


def build_system_parameter_service(session: Session) -> SystemParameterService:
    """装配系统参数公开查询 Service。"""

    return SystemParameterService(SQLModelSystemParameterRepository(session))


__all__ = ["build_system_parameter_service"]
