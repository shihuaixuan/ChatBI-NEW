"""旧 System 用户 ORM 导入路径兼容层。"""

from apps.access_control.models import (
    BaseUserPO,
    UserModel,
    UserPlatformBase,
    UserPlatformModel,
)

__all__ = [
    "BaseUserPO",
    "UserModel",
    "UserPlatformBase",
    "UserPlatformModel",
]
