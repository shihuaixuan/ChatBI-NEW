"""权限变量旧 ORM 导入路径兼容。"""

from apps.access_control.models import AccessVariableModel as SystemVariable

__all__ = ["SystemVariable"]
