from pathlib import Path

from sqlmodel import Session

from apps.datasource.repository.connectors.connection_gateway import (
    DatabaseDriverConnectionGateway,
)
from apps.datasource.repository.connectors.excel_import import (
    PostgreSQLExcelImportGateway,
)
from apps.datasource.repository.connectors.maintenance_gateway import (
    DatabaseDatasourceMaintenanceGateway,
)
from apps.datasource.repository.sqlmodel import (
    SQLModelDatasourceConnectionRepository,
    SQLModelDatasourceMetadataRepository,
    SQLModelDatasourceRepository,
)
from apps.datasource.services import (
    DatasourceConnectionService,
    DatasourceMetadataService,
    DatasourceService,
    ExcelImportService,
)


def build_datasource_connection_service(
    session: Session,
) -> DatasourceConnectionService:
    """在应用边界装配数据源连接 Service。"""

    return DatasourceConnectionService(
        SQLModelDatasourceConnectionRepository(session),
        DatabaseDriverConnectionGateway(),
    )


def build_datasource_metadata_service(
    session: Session,
) -> DatasourceMetadataService:
    """装配物理元数据维护 Service。"""

    return DatasourceMetadataService(
        SQLModelDatasourceMetadataRepository(session),
        build_datasource_connection_service(session),
    )


def build_datasource_service(session: Session) -> DatasourceService:
    """装配数据源基本信息维护 Service。"""

    return DatasourceService(
        SQLModelDatasourceRepository(session),
        build_datasource_metadata_service(session),
        DatabaseDatasourceMaintenanceGateway(),
    )


def build_excel_import_service(upload_directory: str | Path) -> ExcelImportService:
    """装配 Excel 导入应用流程。"""

    return ExcelImportService(
        PostgreSQLExcelImportGateway(),
        Path(upload_directory),
    )
