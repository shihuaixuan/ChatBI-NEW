from pydantic import BaseModel, ConfigDict, Field


class PhysicalRelationEndpoint(BaseModel):
    """物理表关系边的表和字段端点。"""

    model_config = ConfigDict(extra="allow")

    cell: int
    port: int


class PhysicalRelationCell(BaseModel):
    """前端关系图中的节点或边，保留图组件附加属性。"""

    model_config = ConfigDict(extra="allow")

    id: int | str
    shape: str
    source: PhysicalRelationEndpoint | None = None
    target: PhysicalRelationEndpoint | None = None


class PhysicalRelationResources(BaseModel):
    """校验关系图所需的数据源物理资源集合。"""

    cells: list[PhysicalRelationCell] = Field(default_factory=list)
    table_ids: set[int] = Field(default_factory=set)
    field_table_ids: dict[int, int] = Field(default_factory=dict)
