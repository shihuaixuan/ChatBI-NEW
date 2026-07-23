from __future__ import annotations

from typing import Any, Protocol

from apps.dashboard.models import CoreDashboard, DashboardBaseResponse


class DashboardRepository(Protocol):
    """Dashboard 持久化端口。"""

    def list_by_owner(
        self,
        *,
        workspace_id: str,
        creator_id: str,
        node_type: str | None,
    ) -> list[DashboardBaseResponse]: ...

    def get_detail(self, dashboard_id: str) -> dict[str, Any] | None: ...

    def get(self, dashboard_id: str) -> CoreDashboard | None: ...

    def save(self, dashboard: CoreDashboard) -> CoreDashboard: ...

    def name_exists(
        self,
        *,
        workspace_id: str,
        creator_id: str,
        name: str,
        excluded_id: str | None = None,
    ) -> bool: ...

    def delete(self, dashboard_id: str) -> bool: ...
