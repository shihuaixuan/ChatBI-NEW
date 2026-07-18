from typing import Any

from apps.semantic.utils.text import unique_texts


def value_aliases(item: dict[str, Any]) -> list[str]:
    """返回语义值用于匹配的业务名和别名。"""

    return unique_texts(
        [item.get("bizName"), *(item.get("alias") or []), item.get("biz_name")]
    )
