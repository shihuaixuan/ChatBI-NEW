from pydantic import BaseModel, Field


class PhysicalTable(BaseModel):
    """物理表缓存的稳定 DTO。"""

    id: int | None = None
    ds_id: int | None = None
    checked: bool = True
    table_name: str
    table_comment: str | None = ""
    custom_comment: str | None = ""
    embedding: str | None = None


class PhysicalField(BaseModel):
    """物理字段缓存的稳定 DTO。"""

    id: int | None = None
    ds_id: int | None = None
    table_id: int | None = None
    checked: bool = True
    field_name: str
    field_type: str | None = None
    field_comment: str | None = ""
    custom_comment: str | None = ""
    field_index: int = 0


class PhysicalTableSnapshot(BaseModel):
    """一次远端读取完成后的表字段快照。"""

    table_name: str
    table_comment: str | None = ""
    fields: list[PhysicalField] = Field(default_factory=list)


class PhysicalTableDetail(BaseModel):
    """包含字段的物理表查询快照。"""

    table: PhysicalTable
    fields: list[PhysicalField] = Field(default_factory=list)
