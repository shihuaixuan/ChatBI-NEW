from typing import Any

from sqlmodel import Session

from apps.access_control.data_policy import SessionDatasourceQueryPolicyProvider
from apps.dashboard.repository.sqlmodel import SqlModelDashboardRepository
from apps.dashboard.services import DashboardService
from apps.datasource import (
    DatasourceQueryRequest,
    DatasourceQueryStatus,
    DatasourceQuerySubject,
)
from apps.datasource.composition import build_datasource_query_service
from common.core.db import engine
from common.utils.data_format import DataFormat
from common.utils.utils import SQLBotLogUtil


def build_dashboard_service(session: Session) -> DashboardService:
    """组装 Dashboard 应用服务，画布数据直连 Datasource 公开查询能力。"""
    repository = SqlModelDashboardRepository(session)
    query_service = build_datasource_query_service(
        session,
        SessionDatasourceQueryPolicyProvider(lambda: Session(engine)),
    )

    def load_chart_data(
        datasource_id: int | str,
        sql: str,
        subject: DatasourceQuerySubject,
    ) -> dict[str, Any]:
        json_result: dict[str, Any] = {
            "status": "success",
            "data": [],
            "message": "",
        }
        datasource_key = int(datasource_id)
        policy = query_service.resolve_policy(subject, datasource_key)
        result = query_service.execute(
            DatasourceQueryRequest(
                datasource_id=datasource_key,
                sql=sql,
                subject=subject,
                # 已保存图表未记录选表快照，授权表集合是可用的服务端可信上限。
                selected_tables=policy.authorized_tables,
            )
        )
        if result.status != DatasourceQueryStatus.SUCCEEDED or result.data is None:
            SQLBotLogUtil.error(f"Function failed: {result.message}")
            json_result["status"] = "failed"
            json_result["message"] = result.message
            return json_result
        data = DataFormat.convert_large_numbers_in_object_array(  # type: ignore[no-untyped-call]
            result.data.full_data
        )
        json_result["data"] = (
            DataFormat.normalize_qualified_sql_column_keys_in_object_array(data)
        )
        return json_result

    return DashboardService(repository, load_chart_data)
