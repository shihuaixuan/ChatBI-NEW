"""Datasource 领域公开契约。"""

from apps.datasource.catalog import build_datasource_catalog
from apps.datasource.contracts import (
    DatasourceCatalog,
    DatasourcePolicyCatalog,
    DatasourcePolicyField,
    DatasourcePolicySchema,
    DatasourcePolicyTable,
    DatasourceRecommendationConfigStore,
    DatasourceSummary,
    ExternalDatasource,
    ExternalDatasourceField,
    ExternalDatasourceTable,
)
from apps.datasource.external_connection import (
    build_external_datasource_configuration,
    build_external_datasource_connection,
    get_database_type_name,
)
from apps.datasource.models.dto import (
    DatasourceConnection,
    DatasourceRecord,
    PhysicalRelationCell,
    PhysicalTableDetail,
)
from apps.datasource.policy_catalog import build_datasource_policy_catalog

__all__ = [
    "DatasourceCatalog",
    "DatasourceConnection",
    "DatasourceRecord",
    "DatasourcePolicyCatalog",
    "DatasourcePolicyField",
    "DatasourcePolicySchema",
    "DatasourcePolicyTable",
    "DatasourceRecommendationConfigStore",
    "DatasourceSummary",
    "ExternalDatasource",
    "ExternalDatasourceField",
    "ExternalDatasourceTable",
    "PhysicalRelationCell",
    "PhysicalTableDetail",
    "build_datasource_catalog",
    "build_datasource_policy_catalog",
    "build_external_datasource_connection",
    "build_external_datasource_configuration",
    "get_database_type_name",
]
