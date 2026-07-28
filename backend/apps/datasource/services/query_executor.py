"""Datasource 连接服务到安全查询执行端口的适配。"""

from apps.datasource.models.dto import DatasourceDriverResult
from apps.datasource.services.connection_service import (
    DatasourceConnectionService,
    DatasourceNotFoundError,
)
from common.error import ParseSQLResultError


class ConnectionDatasourceQueryExecutor:
    """只执行查询服务已经验证的 SQL，并归一已知驱动错误。"""

    def __init__(self, connection_service: DatasourceConnectionService) -> None:
        self._connection_service = connection_service

    def execute(
        self,
        datasource_id: int,
        sql: str,
        *,
        timeout_seconds: float | None = None,
    ) -> DatasourceDriverResult:
        try:
            payload = self._connection_service.execute_query(
                datasource_id,
                sql,
                origin_column=False,
                timeout_seconds=timeout_seconds,
            )
        except DatasourceNotFoundError:
            return DatasourceDriverResult(
                succeeded=False,
                error_code="datasource_not_found",
                message="数据源不存在",
            )
        except TimeoutError as exc:
            return DatasourceDriverResult(
                succeeded=False,
                error_code="datasource_query_timeout",
                message=str(exc),
                transient=True,
                timed_out=True,
            )
        except ConnectionError as exc:
            return DatasourceDriverResult(
                succeeded=False,
                error_code="datasource_temporarily_unavailable",
                message=str(exc),
                transient=True,
            )
        except ParseSQLResultError as exc:
            return DatasourceDriverResult(
                succeeded=False,
                error_code="sql_result_parse_error",
                message=str(exc),
            )
        return DatasourceDriverResult(succeeded=True, payload=payload)


__all__ = ["ConnectionDatasourceQueryExecutor"]
