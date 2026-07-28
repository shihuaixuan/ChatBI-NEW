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
    SQLModelDatasourcePhysicalRelationRepository,
    SQLModelDatasourceRecommendationConfigStore,
    SQLModelDatasourceRepository,
)
from apps.datasource.services import (
    ConnectionDatasourceQueryExecutor,
    DatasourceConnectionService,
    DatasourceMetadataService,
    DatasourcePhysicalRelationService,
    DatasourceQueryPolicyProvider,
    DatasourceQueryService,
    DatasourceService,
    ExcelImportService,
)


def build_datasource_query_service(
    session: Session,
    policy_provider: DatasourceQueryPolicyProvider,
    *,
    default_limit: int | None = 100,
    sample_rows: int = 10,
) -> DatasourceQueryService:
    """装配必须具有权限提供者的安全查询服务。"""

    connection_service = build_datasource_connection_service(session)
    return DatasourceQueryService(
        policy_provider,
        ConnectionDatasourceQueryExecutor(connection_service),
        default_limit=default_limit,
        sample_rows=sample_rows,
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


def build_datasource_physical_relation_service(
    session: Session,
) -> DatasourcePhysicalRelationService:
    """装配数据源物理表关系维护 Service。"""

    return DatasourcePhysicalRelationService(
        SQLModelDatasourcePhysicalRelationRepository(session)
    )


def build_excel_import_service(upload_directory: str | Path) -> ExcelImportService:
    """装配 Excel 导入应用流程。"""

    return ExcelImportService(
        PostgreSQLExcelImportGateway(),
        Path(upload_directory),
    )


def build_datasource_recommendation_config_store(
    session: Session,
) -> SQLModelDatasourceRecommendationConfigStore:
    """装配供推荐问题共享事务使用的数据源配置端口。"""

    return SQLModelDatasourceRecommendationConfigStore(session)
