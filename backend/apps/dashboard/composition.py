from typing import Any

from sqlmodel import Session

from apps.dashboard.repository.sqlmodel import SqlModelDashboardRepository
from apps.dashboard.services import DashboardService
from apps.datasource.composition import build_datasource_connection_service
from common.utils.data_format import DataFormat
from common.utils.utils import SQLBotLogUtil


def build_dashboard_service(session: Session) -> DashboardService:
    """组装 Dashboard 应用服务，画布数据直连 Datasource 公开查询能力。"""
    repository = SqlModelDashboardRepository(session)
    connection_service = build_datasource_connection_service(session)

    def load_chart_data(datasource_id: int | str, sql: str) -> dict[str, Any]:
        json_result: dict[str, Any] = {
            "status": "success",
            "data": [],
            "message": "",
        }
        try:
            result = connection_service.execute_query(
                int(datasource_id),
                sql,
                origin_column=False,
            )
            _data = DataFormat.convert_large_numbers_in_object_array(  # type: ignore[no-untyped-call]
                result.get("data")
            )
            _data = DataFormat.normalize_qualified_sql_column_keys_in_object_array(
                _data
            )
            json_result["data"] = _data
        except Exception as e:
            SQLBotLogUtil.error(f"Function failed: {e}")
            json_result["status"] = "failed"
            json_result["message"] = f"{e}"
        return json_result

    return DashboardService(repository, load_chart_data)
