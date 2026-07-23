from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardBaseResponse(BaseModel):
    id: str | None = None
    name: str | None = None
    pid: str | None = None
    node_type: str | None = None
    leaf: bool | None = False
    type: str | None = None
    create_time: int | None = None
    update_time: int | None = None
    children: list[DashboardBaseResponse] = Field(default_factory=list)


class BaseDashboard(BaseModel):
    id: str = ""
    name: str = ""
    pid: str = ""
    workspace_id: str = ""
    org_id: str = ""
    type: str = ""
    node_type: str = ""
    level: int = 0
    create_by: int = 0


class QueryDashboard(BaseDashboard):
    opt: str = ""


class CreateDashboard(QueryDashboard):
    canvas_style_data: str = ""
    component_data: str = ""
    canvas_view_info: str = ""
    description: str = ""
