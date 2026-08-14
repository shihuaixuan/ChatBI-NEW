"""时间表达识别与文本标准化。"""

from __future__ import annotations

from typing import Any

from apps.temporal.jionlp_adapter import extract_jionlp_time_entities

_TIME_KEYWORDS = (
    "今天",
    "今日",
    "昨天",
    "昨日",
    "明天",
    "本周",
    "上周",
    "本月",
    "这个月",
    "上月",
    "上个月",
    "本季度",
    "上季度",
    "今年",
    "本年",
    "去年",
    "按天",
    "按周",
    "按月",
    "按季度",
    "按年",
    "today",
    "yesterday",
    "tomorrow",
)
_NORMALIZED_TIME_KEYWORDS = {"".join(keyword.split()).lower() for keyword in _TIME_KEYWORDS}


def normalize_time_text(value: Any) -> str:
    """移除时间表达内部空白，保留原有字符供确定性匹配。"""

    return "".join(str(value or "").strip().split())


def is_time_expression(value: Any) -> bool:
    """判断普通维度值是否实际是时间表达。"""

    text = normalize_time_text(value)
    if not text:
        return False
    if text.lower() in _NORMALIZED_TIME_KEYWORDS:
        return True
    entities = extract_jionlp_time_entities(text)
    if len(entities) != 1:
        return False
    entity = entities[0]
    entity_text = normalize_time_text(entity.get("text"))
    if entity_text == text:
        return True
    if text.startswith("最") and entity_text == text[1:]:
        return True
    return text.startswith(entity_text) and text[len(entity_text) :] in {
        "每天",
        "每日",
        "按天",
        "按日",
        "按周",
        "按月",
        "按季度",
        "按年",
    }


__all__ = [
    "is_time_expression",
    "normalize_time_text",
]
