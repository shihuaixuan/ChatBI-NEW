from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, cast

import orjson

from apps.dashboard.models import (
    CoreDashboard,
    CreateDashboard,
    DashboardBaseResponse,
    QueryDashboard,
)
from apps.dashboard.repository import DashboardRepository
from apps.datasource import DatasourceQuerySubject
from common.core.deps import CurrentUser
from common.utils.tree_utils import build_tree_generic


class DashboardService:
    """Dashboard 应用服务，集中处理权限和画布数据装配。"""

    def __init__(
        self,
        repository: DashboardRepository,
        load_chart_data: Callable[
            [int | str, str, DatasourceQuerySubject],
            dict[str, Any],
        ],
    ) -> None:
        self._repository = repository
        self._load_chart_data = load_chart_data

    def list_resource(
        self,
        dashboard: QueryDashboard,
        current_user: CurrentUser,
    ) -> list[DashboardBaseResponse]:
        workspace_id = str(
            current_user.oid if current_user.oid is not None else 1
        )
        nodes = self._repository.list_by_owner(
            workspace_id=workspace_id,
            creator_id=str(current_user.id),
            node_type=dashboard.node_type or None,
        )
        return cast(
            list[DashboardBaseResponse],
            build_tree_generic(cast(Any, nodes), root_pid="root"),
        )

    def load_resource(
        self,
        dashboard: QueryDashboard,
        current_user: CurrentUser,
    ) -> dict[str, Any] | None:
        resource = self._repository.get_detail(dashboard.id)
        if resource is None:
            return None
        if resource.get("create_by") != str(current_user.id):
            raise PermissionError("DASHBOARD_ACCESS_DENIED")
        canvas_view_info = resource.get("canvas_view_info")
        if not canvas_view_info:
            return resource
        subject = DatasourceQuerySubject(
            user_id=current_user.id,
            workspace_id=current_user.oid if current_user.oid is not None else 1,
        )
        canvas_view = orjson.loads(canvas_view_info)
        for item in canvas_view.values():
            if item.get("datasource") is None or item.get("sql") is None:
                continue
            data_result = self._load_chart_data(
                item["datasource"],
                item["sql"],
                subject,
            )
            item["data"]["data"] = data_result["data"]
            item["status"] = data_result["status"]
            item["message"] = data_result["message"]
        resource["canvas_view_info"] = orjson.dumps(canvas_view)
        return resource

    def create_resource(
        self,
        user: CurrentUser,
        dashboard: CreateDashboard,
    ) -> CoreDashboard:
        return self._repository.save(self._new_record(user, dashboard))

    def update_resource(
        self,
        user: CurrentUser,
        dashboard: QueryDashboard,
    ) -> CoreDashboard:
        record = self._required_record(dashboard.id)
        record.name = dashboard.name
        record.update_by = str(user.id)
        record.update_time = int(time.time())
        return self._repository.save(record)

    def create_canvas(
        self,
        user: CurrentUser,
        dashboard: CreateDashboard,
    ) -> CoreDashboard:
        return self._repository.save(self._new_record(user, dashboard))

    def update_canvas(
        self,
        user: CurrentUser,
        dashboard: CreateDashboard,
    ) -> CoreDashboard:
        record = self._required_record(dashboard.id)
        record.name = dashboard.name
        record.update_by = str(user.id)
        record.update_time = int(time.time())
        record.component_data = dashboard.component_data
        record.canvas_style_data = dashboard.canvas_style_data
        record.canvas_view_info = dashboard.canvas_view_info
        return self._repository.save(record)

    def validate_name(
        self,
        user: CurrentUser,
        dashboard: QueryDashboard,
    ) -> bool:
        if not dashboard.opt:
            raise ValueError("opt is required")
        if dashboard.opt in {"newLeaf", "newFolder"}:
            excluded_id = None
        elif dashboard.opt in {"updateLeaf", "updateFolder", "rename"}:
            if not dashboard.id:
                raise ValueError("id is required for update operation")
            excluded_id = dashboard.id
        else:
            raise ValueError(f"Invalid opt value: {dashboard.opt}")
        workspace_id = str(user.oid if user.oid is not None else 1)
        return not self._repository.name_exists(
            workspace_id=workspace_id,
            creator_id=str(user.id),
            name=dashboard.name,
            excluded_id=excluded_id,
        )

    def delete_resource(self, current_user: CurrentUser, resource_id: str) -> bool:
        record = self._required_record(resource_id)
        if record.create_by != str(current_user.id):
            raise ValueError(
                f"Resource with id {resource_id} not owned by the current user"
            )
        return self._repository.delete(resource_id)

    @staticmethod
    def _new_record(
        user: CurrentUser,
        dashboard: CreateDashboard,
    ) -> CoreDashboard:
        record = CoreDashboard(**dashboard.model_dump())
        record.id = uuid.uuid4().hex
        record.workspace_id = (
            str(user.oid) if user.oid is not None else None
        )
        record.create_by = str(user.id)
        record.create_time = int(time.time())
        return record

    def _required_record(self, dashboard_id: str) -> CoreDashboard:
        record = self._repository.get(dashboard_id)
        if record is None:
            raise ValueError(f"Resource with id {dashboard_id} does not exist")
        return record
