"""旧解析模块的兼容出口，新代码应从 apps.temporal 导入。"""

from apps.temporal.resolver import resolve_time_range, resolve_time_range_payload

__all__ = ["resolve_time_range", "resolve_time_range_payload"]
