from importlib import import_module
from typing import TYPE_CHECKING, Any

from apps.capabilities.sql.permission import PermissionTool
from apps.capabilities.sql.repair import SQLRepairDecision, SQLRepairStrategy
from apps.capabilities.sql.validator import SqlValidateTool

if TYPE_CHECKING:
    from apps.capabilities.sql.executor import GuardedSqlExecutor, SqlExecuteTool


def __getattr__(name: str) -> Any:
    """延迟加载兼容执行入口，避免能力包与 ChatBI Service 循环导入。"""

    if name in {"GuardedSqlExecutor", "SqlExecuteTool"}:
        return getattr(import_module("apps.capabilities.sql.executor"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "GuardedSqlExecutor",
    "PermissionTool",
    "SQLRepairDecision",
    "SQLRepairStrategy",
    "SqlExecuteTool",
    "SqlValidateTool",
]
