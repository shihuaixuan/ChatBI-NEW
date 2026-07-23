from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlmodel import Session, col

from apps.dashboard.models import CoreDashboard, DashboardBaseResponse


class SqlModelDashboardRepository:
    """基于 SQLModel 会话的 Dashboard 仓储。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_owner(
        self,
        *,
        workspace_id: str,
        creator_id: str,
        node_type: str | None,
    ) -> list[DashboardBaseResponse]:
        sql = "SELECT id, name, type, node_type, pid, create_time FROM core_dashboard"
        filters = ["workspace_id = :workspace_id", "create_by = :create_by"]
        params: dict[str, str] = {
            "workspace_id": workspace_id,
            "create_by": creator_id,
        }
        if node_type:
            filters.append("node_type = :node_type")
            params["node_type"] = node_type
        sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY create_time DESC"
        result = self._session.execute(text(sql), params)
        return [DashboardBaseResponse(**row) for row in result.mappings()]

    def get_detail(self, dashboard_id: str) -> dict[str, Any] | None:
        sql = text(
            """
            SELECT cd.*,
                   creator.name AS create_name,
                   updater.name AS update_name
            FROM core_dashboard cd
            LEFT JOIN sys_user creator ON cd.create_by = creator.id::varchar
            LEFT JOIN sys_user updater ON cd.update_by = updater.id::varchar
            WHERE cd.id = :dashboard_id
            """
        )
        result = self._session.execute(
            sql, {"dashboard_id": dashboard_id}
        ).mappings().first()
        return dict(result) if result is not None else None

    def get(self, dashboard_id: str) -> CoreDashboard | None:
        return self._session.get(CoreDashboard, dashboard_id)

    def save(self, dashboard: CoreDashboard) -> CoreDashboard:
        self._session.add(dashboard)
        self._session.commit()
        self._session.refresh(dashboard)
        return dashboard

    def name_exists(
        self,
        *,
        workspace_id: str,
        creator_id: str,
        name: str,
        excluded_id: str | None = None,
    ) -> bool:
        conditions = [
            col(CoreDashboard.workspace_id) == workspace_id,
            col(CoreDashboard.create_by) == creator_id,
            col(CoreDashboard.name) == name,
        ]
        if excluded_id is not None:
            conditions.append(col(CoreDashboard.id) != excluded_id)
        query = self._session.query(CoreDashboard).filter(*conditions)
        return bool(self._session.query(query.exists()).scalar())

    def delete(self, dashboard_id: str) -> bool:
        result = self._session.execute(
            text("DELETE FROM core_dashboard WHERE id = :dashboard_id"),
            {"dashboard_id": dashboard_id},
        )
        self._session.commit()
        return bool(getattr(result, "rowcount", 0) > 0)
