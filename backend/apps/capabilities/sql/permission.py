"""兼容导出（台账 B6）：权限透传钩子已迁入 ChatBI 执行子域。"""

from apps.chatbi.services.execution.sql_permission import PermissionTool

__all__ = ["PermissionTool"]
