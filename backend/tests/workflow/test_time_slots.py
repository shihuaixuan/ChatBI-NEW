from apps.semantic import (
    derive_time_bucket,
    is_time_expression,
    normalize_time_range,
)


def test_detects_chinese_time_expressions_without_matching_dimension_values():
    assert is_time_expression("今天") is True
    assert is_time_expression("最近7天") is True
    assert is_time_expression("按月") is True
    assert is_time_expression("2026年6月1日至2026年6月30日") is True
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
    assert normalize_time_range("today") == normalize_time_range("今天")


def test_normalizes_explicit_month_as_left_closed_right_open_range():
    assert normalize_time_range("2026 年 6 月") == {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }


def test_normalizes_explicit_date_as_one_day_range():
    assert normalize_time_range("2026-07-13") == {
        "kind": "absolute_range",
        "start": "2026-07-13",
        "end_exclusive": "2026-07-14",
        "timezone": "Asia/Shanghai",
    }


def test_normalizes_explicit_date_range_as_left_closed_right_open_range():
    expected = {
        "kind": "absolute_range",
        "start": "2026-06-01",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai",
    }

    assert normalize_time_range("2026年6月1日至2026年6月30日") == expected
    assert normalize_time_range("2026-06-01到2026-06-30") == expected
    assert normalize_time_range("2026/06/01~2026/06/30") == expected


def test_rejects_reversed_or_invalid_explicit_date_range():
    assert normalize_time_range("2026年6月30日至2026年6月1日") == {
        "kind": "unsupported",
        "raw": "2026年6月30日至2026年6月1日",
        "timezone": "Asia/Shanghai",
    }
    assert normalize_time_range("2026年2月29日至2026年3月1日") == {
        "kind": "unsupported",
        "raw": "2026年2月29日至2026年3月1日",
        "timezone": "Asia/Shanghai",
    }


def test_derives_time_bucket_only_from_trusted_shape_and_time_dimension():
    assert derive_time_bucket({"time_grain": "day"}, [276]) == {
        "dimension_id": 276,
        "grain": "day",
    }
    assert derive_time_bucket({}, [276]) is None
    assert derive_time_bucket({"time_grain": "hour"}, [276]) is None
    assert derive_time_bucket({"time_grain": "day"}, []) is None


def test_normalizes_recent_days_when_time_grain_suffix_is_in_raw_text():
    assert normalize_time_range("最近 30 天每天") == {
        "kind": "relative_range",
        "unit": "day",
        "amount": 30,
        "anchor": "today",
        "include_current": True,
        "timezone": "Asia/Shanghai",
    }
