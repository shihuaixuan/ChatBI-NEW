from apps.chatbi_workflow.capabilities.adapters.time_slots import (
    is_time_expression,
    normalize_time_range,
)


def test_detects_chinese_time_expressions_without_matching_dimension_values():
    assert is_time_expression("今天") is True
    assert is_time_expression("最近7天") is True
    assert is_time_expression("按月") is True
    assert is_time_expression("1号店铺") is False
    assert is_time_expression("华东") is False


def test_normalizes_single_day_and_recent_days():
    assert normalize_time_range("今天") == {
        "kind": "single_date",
        "anchor": "today",
        "offset_days": 0,
        "timezone": "Asia/Shanghai",
    }
    assert normalize_time_range("昨天") == {
        "kind": "single_date",
        "anchor": "today",
        "offset_days": -1,
        "timezone": "Asia/Shanghai",
    }
    assert normalize_time_range("最近7天") == {
        "kind": "relative_range",
        "unit": "day",
        "amount": 7,
        "anchor": "today",
        "include_current": True,
        "timezone": "Asia/Shanghai",
    }


def test_normalizes_explicit_month_as_left_closed_right_open_range():
    assert normalize_time_range("2026 年 6 月") == {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }


def test_normalizes_recent_days_when_time_grain_suffix_is_in_raw_text():
    assert normalize_time_range("最近 30 天每天") == {
        "kind": "relative_range",
        "unit": "day",
        "amount": 30,
        "anchor": "today",
        "include_current": True,
        "timezone": "Asia/Shanghai",
    }
