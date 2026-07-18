"""兼容已安装 XPack 的固定导入路径，不映射旧术语表。"""

from apps.semantic.xpack_terminology_compatibility import XpackTerminology

Terminology = XpackTerminology

__all__ = ["Terminology"]
