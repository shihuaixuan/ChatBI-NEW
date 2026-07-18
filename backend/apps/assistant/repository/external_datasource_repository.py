"""Assistant 外部数据源目录端口。"""

from collections.abc import Callable
from typing import Protocol

from apps.datasource import ExternalDatasource


class ExternalDatasourceCatalog(Protocol):
    """定义 Assistant 业务服务需要的外部数据源能力。"""

    ds_list: list[ExternalDatasource]

    def get_simple_ds_list(self) -> list[dict[str, object]]: ...

    def get_ds(
        self,
        datasource_id: int,
        trans: Callable[..., str] | None = None,
    ) -> ExternalDatasource: ...

    def get_db_schema(
        self,
        datasource_id: int,
        question: str = "",
        embedding: bool = True,
        table_list: list[str] | None = None,
    ) -> str: ...
