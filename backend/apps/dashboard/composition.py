from sqlmodel import Session

from apps.chatbi import get_chart_data_ds
from apps.dashboard.repository.sqlmodel import SqlModelDashboardRepository
from apps.dashboard.services import DashboardService


def build_dashboard_service(session: Session) -> DashboardService:
    """组装 Dashboard 应用服务。"""
    repository = SqlModelDashboardRepository(session)
    return DashboardService(
        repository,
        lambda datasource_id, sql: get_chart_data_ds(
            session, datasource_id, sql
        ),
    )
