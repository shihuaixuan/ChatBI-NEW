# 实现已下沉到共享能力层；本文件仅保持既有 import 路径兼容（graph 与 v1 均引用此路径）。
# deprecated：新代码一律 from apps.chatbi_capabilities.sql.permission import PermissionTool。
from apps.chatbi_capabilities.sql.permission import PermissionTool

__all__ = ["PermissionTool"]
