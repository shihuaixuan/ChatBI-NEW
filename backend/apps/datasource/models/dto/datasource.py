from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.datasource.models.dto.physical_schema import PhysicalField, PhysicalTable


class CreateDatasource(BaseModel):
    """创建数据源时使用的兼容请求 DTO。"""

    id: int | None = None
    name: str = ""
    description: str = ""
    type: str = ""
    configuration: str = ""
    create_time: datetime | None = None
    create_by: int = 0
    status: str = ""
    num: str = ""
    oid: int = 1
    tables: list[PhysicalTable] = Field(default_factory=list)
    recommended_config: int = 1


class DatasourceRecord(BaseModel):
    """跨应用层使用的数据源基本信息快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    name: str = ""
    description: str | None = ""
    type: str = ""
    type_name: str | None = None
    configuration: str = ""
    create_time: datetime | None = None
    create_by: int = 0
    status: str | None = ""
    num: str | None = ""
    oid: int = 1
    table_relation: list[Any] | None = None
    embedding: str | None = None
    recommended_config: int = 1


class UpdateDatasource(BaseModel):
    """数据源基本信息修改请求。"""

    id: int
    name: str | None = None
    description: str | None = None
    type: str | None = None
    configuration: str | None = None
    recommended_config: int | None = None


class TableObj(BaseModel):
    """物理表及字段编辑 DTO。"""

    table: PhysicalTable | None = None
    fields: list[PhysicalField] = Field(default_factory=list)


class FieldObj(BaseModel):
    fieldName: str | None = None


class PreviewResponse(BaseModel):
    fields: list[Any] | None = Field(default_factory=list)
    data: list[Any] | None = Field(default_factory=list)
    sql: str | None = ""


class FieldInfo(BaseModel):
    fieldName: object
    fieldType: str


class SheetFields(BaseModel):
    sheetName: str
    fields: list[FieldInfo]


class ImportRequest(BaseModel):
    filePath: str
    sheets: list[SheetFields]
