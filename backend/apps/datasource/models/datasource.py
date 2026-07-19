"""Datasource 历史模型导入兼容层。

新代码应分别从 ``models.orm`` 和 ``models.dto`` 导入。这里不再定义模型，
只保证迁移期间旧调用方引用的是同一对象。
"""

from apps.datasource.models.dto import (
    ColumnSchema,
    ColumnSchemaResponse,
    CreateDatasource,
    DatasourceConf,
    FieldInfo,
    FieldObj,
    ImportRequest,
    PreviewResponse,
    RecommendedProblemBase,
    RecommendedProblemBaseChat,
    RecommendedProblemResponse,
    SheetFields,
    TableAndFields,
    TableObj,
    TableSchema,
    TableSchemaResponse,
)
from apps.datasource.models.orm import (
    CoreDatasource,
    CoreField,
    CoreTable,
    DsRecommendedProblem,
)

__all__ = [
    "ColumnSchema",
    "ColumnSchemaResponse",
    "CoreDatasource",
    "CoreField",
    "CoreTable",
    "CreateDatasource",
    "DatasourceConf",
    "DsRecommendedProblem",
    "FieldInfo",
    "FieldObj",
    "ImportRequest",
    "PreviewResponse",
    "RecommendedProblemBase",
    "RecommendedProblemBaseChat",
    "RecommendedProblemResponse",
    "SheetFields",
    "TableAndFields",
    "TableObj",
    "TableSchema",
    "TableSchemaResponse",
]
