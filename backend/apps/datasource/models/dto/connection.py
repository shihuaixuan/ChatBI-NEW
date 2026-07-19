from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.datasource.models.dto.physical_schema import PhysicalField, PhysicalTable


class DatasourceConnection(BaseModel):
    """连接器所需的最小数据源快照，不向调用方暴露 ORM。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    type: str
    type_name: str | None = None
    configuration: str


class DatasourceConf(BaseModel):
    """数据库连接器使用的标准配置 DTO。"""

    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    database: str = ""
    driver: str = ""
    extraJdbc: str = ""
    dbSchema: str = ""
    filename: str = ""
    sheets: list[Any] = Field(default_factory=list)
    mode: str = ""
    timeout: int = 30
    lowVersion: bool = False
    ssl: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class TableSchema:
    def __init__(self, attr1: str, attr2: str | bytes | None = None) -> None:
        self.tableName = attr1
        self.tableComment = (
            attr2 if attr2 is None or isinstance(attr2, str) else attr2.decode("utf-8")
        )

    tableName: str
    tableComment: str | None


class TableSchemaResponse(BaseModel):
    tableName: str = ""
    tableComment: str | None = ""


class ColumnSchema:
    def __init__(
        self,
        attr1: str,
        attr2: str,
        attr3: str | bytes | None,
    ) -> None:
        self.fieldName = attr1
        self.fieldType = attr2
        self.fieldComment = (
            attr3 if attr3 is None or isinstance(attr3, str) else attr3.decode("utf-8")
        )

    fieldName: str
    fieldType: str
    fieldComment: str | None


class ColumnSchemaResponse(BaseModel):
    fieldName: str | None = ""
    fieldType: str | None = ""
    fieldComment: str | None = ""


class TableAndFields:
    def __init__(
        self,
        schema: str,
        table: PhysicalTable,
        fields: list[PhysicalField],
    ) -> None:
        self.schema = schema
        self.table = table
        self.fields = fields

    schema: str
    table: PhysicalTable
    fields: list[PhysicalField]
