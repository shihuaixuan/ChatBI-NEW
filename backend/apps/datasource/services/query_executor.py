"""Datasource 连接服务到安全查询执行端口的适配。"""

from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

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
        except (TimeoutError, SQLAlchemyTimeoutError) as exc:
            return DatasourceDriverResult(
                succeeded=False,
                error_code="datasource_query_timeout",
                message=str(exc),
                transient=True,
                timed_out=True,
            )
        except DBAPIError as exc:
            if _is_driver_timeout(exc):
                return DatasourceDriverResult(
                    succeeded=False,
                    error_code="datasource_query_timeout",
                    message=str(exc),
                    transient=True,
                    timed_out=True,
                )
            transient = exc.connection_invalidated or _is_connection_error(exc.orig)
            return DatasourceDriverResult(
                succeeded=False,
                error_code=(
                    "datasource_temporarily_unavailable"
                    if transient
                    else "datasource_query_error"
                ),
                message=str(exc),
                transient=transient,
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


def _is_driver_timeout(exc: DBAPIError) -> bool:
    """识别被 SQLAlchemy 包装的驱动超时。"""

    if isinstance(exc.orig, TimeoutError):
        return True
    message = str(exc.orig or exc).lower()
    return any(marker in message for marker in ("timed out", "timeout", "time out"))


def _is_connection_error(error: Any) -> bool:
    """识别 MySQL 系驱动常见的连接中断错误码。"""

    args = getattr(error, "args", ())
    code = args[0] if args and isinstance(args[0], int) else None
    return code in {2002, 2003, 2006, 2013}


__all__ = ["ConnectionDatasourceQueryExecutor"]
