"""时间表达识别与文本标准化。"""

from __future__ import annotations

import re
from typing import Any

ABSOLUTE_DATE_PATTERN = (
    r"(\d{4})(?:年|[./-])(\d{1,2})(?:月|[./-])(\d{1,2})日?"
)

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
    "本财年",
    "当前财年",
    "本财政年度",
    "上财年",
    "上一财年",
    "上个财年",
    "上一财政年度",
    "本财季",
    "当前财季",
    "本财政季度",
    "上财季",
    "上一财季",
    "上个财季",
    "上一财政季度",
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
_NORMALIZED_TIME_KEYWORDS = {
    re.sub(r"\s+", "", keyword).lower() for keyword in _TIME_KEYWORDS
}


def normalize_time_text(value: Any) -> str:
    """移除时间表达内部空白，保留原有字符供确定性匹配。"""

    return re.sub(r"\s+", "", str(value or "").strip())


def is_time_expression(value: Any) -> bool:
    """判断普通维度值是否实际是时间表达。"""

    text = normalize_time_text(value)
    if not text:
        return False
    if text.lower() in _NORMALIZED_TIME_KEYWORDS:
        return True
    if re.fullmatch(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?", text):
        return True
    if re.fullmatch(ABSOLUTE_DATE_PATTERN, text):
        return True
    if re.fullmatch(
        rf"{ABSOLUTE_DATE_PATTERN}(?:至|到|~|～|—|–){ABSOLUTE_DATE_PATTERN}",
        text,
    ):
        return True
    if re.fullmatch(r"(?:\d{4}(?:财年|财政年度)|FY\d{4})(?:第?[1-4](?:季度|季)|Q[1-4])?", text, re.IGNORECASE):
        return True
    return re.fullmatch(r"(最近|近)\d+(天|日|周|个月|月|年)", text) is not None


__all__ = [
    "ABSOLUTE_DATE_PATTERN",
    "is_time_expression",
    "normalize_time_text",
]
