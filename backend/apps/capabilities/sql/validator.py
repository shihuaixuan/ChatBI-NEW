"""兼容导出（台账 B6）：SQL 校验器已迁入 ChatBI 执行子域。"""

from apps.chatbi.services.execution.sql_validator import SqlValidateTool

__all__ = ["SqlValidateTool"]
