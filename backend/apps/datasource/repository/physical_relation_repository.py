from typing import Protocol

from apps.datasource.models.dto import (
    PhysicalRelationCell,
    PhysicalRelationResources,
)


class DatasourcePhysicalRelationRepository(Protocol):
    """数据源物理表关系图的仓储端口。"""

    def get_resources(
        self,
        datasource_id: int,
    ) -> PhysicalRelationResources | None: ...

    def save(
        self,
        datasource_id: int,
        cells: list[PhysicalRelationCell],
    ) -> None: ...
