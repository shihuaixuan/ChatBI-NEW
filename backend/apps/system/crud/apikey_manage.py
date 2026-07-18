
"""API Key 旧导入路径兼容层。"""

from apps.access_control.cache import clear_api_key_cache, get_api_key

__all__ = ["clear_api_key_cache", "get_api_key"]
