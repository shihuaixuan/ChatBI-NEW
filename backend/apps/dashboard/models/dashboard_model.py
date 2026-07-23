"""Dashboard 旧模型入口，仅用于兼容外部扩展。"""

from apps.dashboard.models.dto import (
    BaseDashboard,
    CreateDashboard,
    DashboardBaseResponse,
    QueryDashboard,
)
from apps.dashboard.models.orm import CoreDashboard

__all__ = [
    "BaseDashboard",
    "CoreDashboard",
    "CreateDashboard",
    "DashboardBaseResponse",
    "QueryDashboard",
]
