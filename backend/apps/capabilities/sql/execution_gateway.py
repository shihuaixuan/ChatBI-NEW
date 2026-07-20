"""兼容导出（台账 B6）：SQL 执行适配器已迁入 ChatBI adapters。"""

from apps.chatbi.adapters.execution import SqlExecuteTool

__all__ = ["SqlExecuteTool"]
