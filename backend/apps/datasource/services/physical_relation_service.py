from apps.datasource.models.dto import PhysicalRelationCell
from apps.datasource.models.rules.physical_relation import (
    validate_physical_relation_cells,
)
from apps.datasource.repository.physical_relation_repository import (
    DatasourcePhysicalRelationRepository,
)
from apps.datasource.services.connection_service import DatasourceNotFoundError


class DatasourcePhysicalRelationService:
    """数据源物理表关系图读取、校验和保存入口。"""

    def __init__(self, repository: DatasourcePhysicalRelationRepository) -> None:
        self._repository = repository

    def list_relations(self, datasource_id: int) -> list[PhysicalRelationCell]:
        resources = self._repository.get_resources(datasource_id)
        if resources is None:
            raise DatasourceNotFoundError(datasource_id)
        return resources.cells

    def save_relations(
        self,
        datasource_id: int,
        cells: list[PhysicalRelationCell],
    ) -> None:
        resources = self._repository.get_resources(datasource_id)
        if resources is None:
            raise DatasourceNotFoundError(datasource_id)

        validate_physical_relation_cells(
            datasource_id,
            cells,
            resources.table_ids,
            resources.field_table_ids,
        )
        self._repository.save(datasource_id, cells)
