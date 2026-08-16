"""Semantic 公开服务的依赖装配入口。"""

from sqlmodel import Session

from apps.semantic.repository.datasource.metadata_repository import (
    SqlModelDatasourceMetadataRepository,
)
from apps.semantic.repository.excel.term_workbook_repository import (
    ExcelTermWorkbookRepository,
)
from apps.semantic.repository.sqlmodel.dataset_binding_repository import (
    SQLModelDatasetBindingRepository,
)
from apps.semantic.repository.sqlmodel.dataset_catalog_repository import (
    SQLModelDatasetCatalogRepository,
)
from apps.semantic.repository.sqlmodel.domain_repository import (
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.instruction_repository import (
    SqlModelInstructionRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import SemanticSchemaLoader
from apps.semantic.repository.sqlmodel.semantic_contract_repository import (
    SqlModelSemanticContractRepository,
)
from apps.semantic.repository.sqlmodel.term_repository import SqlModelTermRepository
from apps.semantic.services.contract_build_service import SemanticContractBuildService
from apps.semantic.services.contract_publication_service import (
    SemanticContractPublicationService,
)
from apps.semantic.services.dataset_binding_service import (
    SemanticDatasetBindingService,
)
from apps.semantic.services.dataset_catalog_service import (
    SemanticDatasetCatalogService,
)
from apps.semantic.services.dataset_reference_service import (
    SemanticDatasetReferenceService,
)
from apps.semantic.services.instruction_service import SemanticInstructionService
from apps.semantic.services.schema_service import SemanticSchemaService
from apps.semantic.services.semantic_contract_service import SemanticContractService
from apps.semantic.services.sql_compilation_service import (
    SemanticSQLCompilationService,
)
from apps.semantic.services.sql_compiler import SemanticSQLCompiler
from apps.semantic.services.term_compatibility_service import (
    LegacyTerminologyCompatibilityService,
)
from apps.semantic.services.term_excel_service import SemanticTermExcelService
from apps.semantic.services.term_query_service import SemanticTermQueryService
from apps.semantic.services.term_service import SemanticTermService


def build_semantic_term_query_service(
    session: Session,
) -> SemanticTermQueryService:
    """为跨领域调用方装配只读术语查询服务。"""

    return SemanticTermQueryService(
        SemanticSchemaService(SemanticSchemaLoader(session))
    )


def build_semantic_contract_service(session: Session) -> SemanticContractService:
    """装配完整语义契约的基础管理服务。"""

    return SemanticContractService(
        SqlModelSemanticContractRepository(session),
        SqlModelDomainRepository(session),
    )


def build_semantic_contract_build_service(
    session: Session,
) -> SemanticContractBuildService:
    """装配从物理元数据统一构建语义契约的服务。"""

    return SemanticContractBuildService(
        SqlModelSemanticContractRepository(session),
        SqlModelDomainRepository(session),
        SqlModelDatasourceMetadataRepository(session),
    )


def build_semantic_contract_publication_service(
    session: Session | None = None,
) -> SemanticContractPublicationService:
    """装配契约报告和发布服务。"""

    repository = (
        SqlModelSemanticContractRepository(session) if session is not None else None
    )
    return SemanticContractPublicationService(repository)


def build_semantic_schema_service(session: Session) -> SemanticSchemaService:
    """装配供跨领域调用方读取语义数据集 Schema 的公开服务。"""

    return SemanticSchemaService(SemanticSchemaLoader(session))


def build_semantic_instruction_service(session: Session) -> SemanticInstructionService:
    """装配数据集 instructions 管理服务。"""

    return SemanticInstructionService(SqlModelInstructionRepository(session))


def build_semantic_dataset_binding_service(
    session: Session,
) -> SemanticDatasetBindingService:
    """装配数据集执行绑定公开服务。"""

    return SemanticDatasetBindingService(SQLModelDatasetBindingRepository(session))


def build_semantic_dataset_catalog_service(
    session: Session,
) -> SemanticDatasetCatalogService:
    """装配按 id 读取数据集展示信息的公开服务。"""

    return SemanticDatasetCatalogService(SQLModelDatasetCatalogRepository(session))


def build_semantic_dataset_reference_service(
    session: Session,
) -> SemanticDatasetReferenceService:
    """为跨领域引用校验装配数据集只读目录。"""

    return SemanticDatasetReferenceService(
        SemanticSchemaService(SemanticSchemaLoader(session))
    )


def build_semantic_sql_compilation_service(
    session: Session,
) -> SemanticSQLCompilationService:
    """装配供 ChatBI 使用的 Semantic SQL 编译服务。"""

    return SemanticSQLCompilationService(
        SemanticSchemaService(SemanticSchemaLoader(session)),
        SemanticSQLCompiler(),
    )


def build_legacy_terminology_compatibility_service(
    session: Session,
) -> LegacyTerminologyCompatibilityService:
    """为旧术语 HTTP 路径装配 Semantic 权威管理服务。"""

    return LegacyTerminologyCompatibilityService(
        SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        )
    )


def build_semantic_term_excel_service(
    session: Session,
) -> SemanticTermExcelService:
    """装配 Semantic 术语 Excel 服务。"""

    return SemanticTermExcelService(
        SemanticTermService(
            SqlModelTermRepository(session),
            SqlModelDomainRepository(session),
        ),
        ExcelTermWorkbookRepository(),
    )


__all__ = [
    "build_semantic_schema_service",
    "build_semantic_dataset_binding_service",
    "build_semantic_dataset_catalog_service",
    "build_semantic_dataset_reference_service",
    "build_semantic_sql_compilation_service",
    "build_legacy_terminology_compatibility_service",
    "build_semantic_term_query_service",
    "build_semantic_contract_service",
    "build_semantic_contract_build_service",
    "build_semantic_contract_publication_service",
    "build_semantic_term_excel_service",
]
