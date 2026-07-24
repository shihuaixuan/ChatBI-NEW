"""Conversation 历史结果的展示格式化助手。

这些函数只做纯粹的数据整形（解析 JSON、格式化 SQL、过滤内部字段），
不访问数据库；从原 `chatbi/api/legacy_read.py` 迁入，归会话数据所有者维护。
"""

from typing import Any

import orjson
import sqlparse

from apps.conversation.models import ChatRecordResult
from common.utils.data_format import DataFormat


def _format_column(column: dict[str, Any]) -> str:
    """格式化单个 column 字段。"""

    value = column.get("value", "")
    name = column.get("name", "")
    if value != name and name:
        return f"{value}({name})"
    return str(value)


def format_chart_fields(chart_info: dict[str, Any]) -> list[str]:
    """从图表配置中抽取展示字段列表。"""

    fields: list[str] = []

    for column in chart_info.get("columns") or []:
        fields.append(_format_column(column))

    if axis := chart_info.get("axis"):
        if x_axis := axis.get("x"):
            fields.append(_format_column(x_axis))

        if y_axis := axis.get("y"):
            if isinstance(y_axis, list):
                for column in y_axis:
                    fields.append(_format_column(column))
            else:
                fields.append(_format_column(y_axis))

        if series := axis.get("series"):
            fields.append(_format_column(series))

    return [field for field in fields if field]


def format_json_list_data(origin_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化行数据，超长数字转字符串并统一列名。"""

    data: list[dict[str, Any]] = []
    for _data in origin_data if origin_data else []:
        _row: dict[str, Any] = {}
        for key, value in _data.items():
            if value is not None:
                if isinstance(value, (int, float)):
                    if isinstance(value, int) and len(str(abs(value))) > 15:
                        value = str(value)
                    elif isinstance(value, float):
                        decimal_str = format(value, ".16f").rstrip("0").rstrip(".")
                        if len(decimal_str) > 15:
                            value = str(value)
            _row[key] = value
        data.append(DataFormat.normalize_qualified_sql_column_keys(_row))

    return data


def format_json_data(origin_data: dict[str, Any]) -> dict[str, Any]:
    """规范化 `{fields, data}` 结构的图表数据。"""

    result: dict[str, Any] = {
        "fields": origin_data.get("fields") if origin_data.get("fields") else []
    }
    _list: list[dict[str, Any]] = origin_data.get("data") or []
    result["data"] = format_json_list_data(_list)
    return result


def format_record(record: ChatRecordResult) -> dict[str, Any]:
    """把历史记录整形为前端所需的字典表示。"""

    _dict = record.model_dump()

    if (
        record.sql_answer
        and record.sql_answer.strip() != ""
        and record.sql_answer.strip()[0] == "{"
        and record.sql_answer.strip()[-1] == "}"
    ):
        _obj = orjson.loads(record.sql_answer)
        _dict["sql_answer"] = _obj.get("reasoning_content")
    if record.sql_reasoning_content and record.sql_reasoning_content.strip() != "":
        _dict["sql_answer"] = record.sql_reasoning_content
    if (
        record.chart_answer
        and record.chart_answer.strip() != ""
        and record.chart_answer.strip()[0] == "{"
        and record.chart_answer.strip()[-1] == "}"
    ):
        _obj = orjson.loads(record.chart_answer)
        _dict["chart_answer"] = _obj.get("reasoning_content")
    if record.chart_reasoning_content and record.chart_reasoning_content.strip() != "":
        _dict["chart_answer"] = record.chart_reasoning_content
    if (
        record.analysis
        and record.analysis.strip() != ""
        and record.analysis.strip()[0] == "{"
        and record.analysis.strip()[-1] == "}"
    ):
        _obj = orjson.loads(record.analysis)
        _dict["analysis_thinking"] = _obj.get("reasoning_content")
        _dict["analysis"] = _obj.get("content")
    if (
        record.analysis_reasoning_content
        and record.analysis_reasoning_content.strip() != ""
    ):
        _dict["analysis_thinking"] = record.analysis_reasoning_content
    if (
        record.predict
        and record.predict.strip() != ""
        and record.predict.strip()[0] == "{"
        and record.predict.strip()[-1] == "}"
    ):
        _obj = orjson.loads(record.predict)
        _dict["predict"] = _obj.get("reasoning_content")
        _dict["predict_content"] = _obj.get("content")
    if (
        record.predict_reasoning_content
        and record.predict_reasoning_content.strip() != ""
    ):
        _dict["predict"] = record.predict_reasoning_content
    if record.data and record.data.strip() != "":
        try:
            _dict["data"] = orjson.loads(record.data)
        except Exception:
            pass
    if record.predict_data and record.predict_data.strip() != "":
        try:
            _dict["predict_data"] = orjson.loads(record.predict_data)
        except Exception:
            pass
    if record.sql and record.sql.strip() != "":
        try:
            _dict["sql"] = sqlparse.format(record.sql, reindent=True)
        except Exception:
            pass

    if "duration" in _dict and _dict["duration"] is not None:
        try:
            _dict["duration"] = round(_dict["duration"], 2)
        except Exception:
            pass

    if "total_tokens" in _dict and _dict["total_tokens"] is not None:
        try:
            _dict["total_tokens"] = int(_dict["total_tokens"]) if _dict["total_tokens"] else 0
        except Exception:
            _dict["total_tokens"] = 0

    _dict.pop("sql_reasoning_content", None)
    _dict.pop("chart_reasoning_content", None)
    _dict.pop("analysis_reasoning_content", None)
    _dict.pop("predict_reasoning_content", None)

    return _dict


__all__ = [
    "format_chart_fields",
    "format_json_data",
    "format_json_list_data",
    "format_record",
]
