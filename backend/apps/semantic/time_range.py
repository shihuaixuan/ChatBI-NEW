"""旧语义层时间入口的兼容转发，新代码应从 apps.temporal 导入。"""

from apps.temporal import (
    derive_time_bucket,
    is_time_expression,
    normalize_time_range,
    normalize_time_range_payload,
)

__all__ = [
    "derive_time_bucket",
    "is_time_expression",
    "normalize_time_range",
    "normalize_time_range_payload",
]
