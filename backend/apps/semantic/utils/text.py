from collections.abc import Iterable
from typing import Any


def unique_texts(items: Iterable[Any]) -> list[str]:
    """清理文本集合，并按输入顺序去重。"""
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result
