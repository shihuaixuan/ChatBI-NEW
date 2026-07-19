from datetime import datetime

from apps.datasource.models.dto import (
    CreateDatasource,
    DatasourceRecord,
    UpdateDatasource,
)
from apps.datasource.repository.datasource_repository import DatasourceRepository
from apps.datasource.repository.maintenance_gateway import (
    DatasourceMaintenanceGateway,
)
from apps.datasource.services.connection_service import DatasourceNotFoundError
from apps.datasource.services.metadata_service import DatasourceMetadataService


class DatasourceNameConflictError(ValueError):
    """同一工作空间内的数据源名称重复。"""


class DatasourceService:
    """数据源基本信息创建、查询、修改和删除的统一入口。"""

    def __init__(
        self,
        repository: DatasourceRepository,
        metadata_service: DatasourceMetadataService,
        maintenance_gateway: DatasourceMaintenanceGateway,
    ) -> None:
        self._repository = repository
        self._metadata_service = metadata_service
        self._maintenance_gateway = maintenance_gateway

    def list_by_workspace(self, workspace_id: int) -> list[DatasourceRecord]:
        return self._repository.list_by_workspace(workspace_id)

    def get(self, datasource_id: int) -> DatasourceRecord:
        datasource = self._repository.get(datasource_id)
        if datasource is None:
            raise DatasourceNotFoundError(datasource_id)
        return datasource

    def create(
        self,
        request: CreateDatasource,
        *,
        actor_id: int,
        workspace_id: int,
    ) -> DatasourceRecord:
        self._ensure_name_available(workspace_id, request.name)
        datasource = DatasourceRecord(
            name=request.name,
            description=request.description,
            type=request.type,
            type_name=self._maintenance_gateway.get_type_name(request.type),
            configuration=request.configuration,
            create_time=datetime.now(),
            create_by=actor_id,
            status="Success",
            num=request.num,
            oid=workspace_id,
            recommended_config=request.recommended_config,
        )
        created = self._repository.add_pending(datasource)
        if created.id is None:
            self._repository.rollback()
            raise RuntimeError("Created datasource has no ID")

        try:
            # 数据源记录和物理元数据快照必须由同一个会话事务提交。
            self._metadata_service.sync_selected_tables(created.id, request.tables)
        except Exception:
            self._repository.rollback()
            raise
        return self.get(created.id)

    def update(
        self,
        workspace_id: int,
        request: UpdateDatasource,
    ) -> DatasourceRecord:
        current = self.get(request.id)
        changes = request.model_dump(
            exclude={"id"},
            exclude_none=True,
            exclude_unset=True,
        )
        name = str(changes.get("name", current.name))
        self._ensure_name_available(
            workspace_id,
            name,
            exclude_id=request.id,
        )

        datasource_type = str(changes.get("type", current.type))
        changes["type_name"] = self._maintenance_gateway.get_type_name(datasource_type)
        changes["status"] = "Success"
        updated = current.model_copy(update=changes)
        return self._repository.update(updated)

    def delete(self, datasource_id: int) -> DatasourceRecord:
        datasource = self.get(datasource_id)
        # Excel 物理表属于外部本地数据引擎，先清理成功后再删除元数据记录。
        self._maintenance_gateway.cleanup(datasource)
        self._repository.delete(datasource_id)
        return datasource

    def _ensure_name_available(
        self,
        workspace_id: int,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> None:
        if self._repository.name_exists(
            workspace_id,
            name,
            exclude_id=exclude_id,
        ):
            raise DatasourceNameConflictError(name)
