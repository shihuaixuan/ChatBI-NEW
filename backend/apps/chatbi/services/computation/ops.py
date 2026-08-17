"""ComputeTask 白名单操作到 DuckDB SQL 的编译。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from apps.chatbi.models.dto.analysis_plan import ComputeOperation, ComputeTask
from apps.chatbi.services.computation.errors import ComputeOperationError
from apps.chatbi.services.computation.expr_validator import validate_expression


def compile_operation(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
) -> str:
    """根据 ComputeTask 结构生成固定模板 SQL，不接受裸 SQL。"""

    operation = task.operation
    if operation is ComputeOperation.COMPARE:
        return _compile_compare(task, table_names, schemas, growth=False)
    if operation is ComputeOperation.GROWTH:
        return _compile_compare(task, table_names, schemas, growth=True)
    if operation is ComputeOperation.SHARE:
        return _compile_share(task, table_names, schemas)
    if operation is ComputeOperation.TOPN_OTHER:
        return _compile_topn_other(task, table_names, schemas)
    if operation is ComputeOperation.PIVOT:
        return _compile_pivot(task, table_names, schemas)
    if operation is ComputeOperation.EXPR:
        return _compile_expr(task, table_names, schemas)
    raise ComputeOperationError(f"COMPUTE_OPERATION_NOT_ALLOWED:{operation.value}")


def _compile_compare(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
    *,
    growth: bool,
) -> str:
    _require_inputs(task, 2)
    left_id, right_id = task.inputs[:2]
    left_schema = schemas[left_id]
    right_schema = schemas[right_id]
    keys = (
        _require_columns(task.join_on, left_schema, right_schema, "join_on")
        if task.join_on
        else ()
    )
    metrics = _metrics(task, left_schema, right_schema, keys)
    select_parts = [
        f"COALESCE(l.{_quote(key)}, r.{_quote(key)}) AS {_quote(key)}"
        for key in keys
    ]
    for metric in metrics:
        left_alias = _quote(f"{metric}_base")
        right_alias = _quote(f"{metric}_compare")
        select_parts.extend(
            [
                f"l.{_quote(metric)} AS {left_alias}",
                f"r.{_quote(metric)} AS {right_alias}",
            ]
        )
        if growth:
            select_parts.append(
                "CASE WHEN TRY_CAST(l.{metric} AS DOUBLE) IS NULL "
                "OR ABS(TRY_CAST(l.{metric} AS DOUBLE)) = 0 THEN NULL "
                "ELSE (TRY_CAST(r.{metric} AS DOUBLE) - TRY_CAST(l.{metric} AS DOUBLE)) "
                "/ ABS(TRY_CAST(l.{metric} AS DOUBLE)) END AS {growth}".format(
                    metric=_quote(metric),
                    growth=_quote(f"{metric}_growth"),
                )
            )
    join = " AND ".join(
        f"l.{_quote(key)} IS NOT DISTINCT FROM r.{_quote(key)}" for key in keys
    ) or "TRUE"
    return (
        "SELECT "
        + ", ".join(select_parts)
        + f" FROM {table_names[left_id]} l FULL OUTER JOIN {table_names[right_id]} r ON {join}"
    )


def _compile_share(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
) -> str:
    _require_inputs(task, 1)
    source_id = task.inputs[0]
    schema = schemas[source_id]
    dimensions = _option_columns(task, "dimensions", schema)
    if not dimensions:
        dimension = task.options.get("dimension")
        dimensions = _require_columns((dimension,), schema, schema, "dimension") if dimension else ()
    metrics = _metrics(task, schema, schema, dimensions)
    if len(metrics) != 1:
        raise ComputeOperationError("COMPUTE_SHARE_METRIC_REQUIRED")
    metric = metrics[0]
    select_parts = [f"src.{_quote(field)} AS {_quote(field)}" for field in schema]
    select_parts.append(
        f"CASE WHEN SUM(src.{_quote(metric)}) OVER () = 0 THEN NULL ELSE "
        f"src.{_quote(metric)} / SUM(src.{_quote(metric)}) OVER () END AS {_quote(f'{metric}_share')}"
    )
    return f"SELECT {', '.join(select_parts)} FROM {table_names[source_id]} src"


def _compile_topn_other(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
) -> str:
    _require_inputs(task, 1)
    source_id = task.inputs[0]
    schema = schemas[source_id]
    dimension = task.options.get("dimension")
    dimensions = _require_columns((dimension,), schema, schema, "dimension") if dimension else ()
    if len(dimensions) != 1:
        raise ComputeOperationError("COMPUTE_TOPN_DIMENSION_REQUIRED")
    dimension_name = dimensions[0]
    metrics = _metrics(task, schema, schema, dimensions)
    if len(metrics) != 1:
        raise ComputeOperationError("COMPUTE_TOPN_METRIC_REQUIRED")
    metric = metrics[0]
    top_n = task.options.get("top_n", task.options.get("limit"))
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
        raise ComputeOperationError("COMPUTE_TOPN_LIMIT_INVALID")
    dimension_sql = _quote(dimension_name)
    metric_sql = _quote(metric)
    return (
        "WITH ranked AS (SELECT src."
        + dimension_sql
        + " AS dimension_value, src."
        + metric_sql
        + " AS metric_value, ROW_NUMBER() OVER (ORDER BY src."
        + metric_sql
        + " DESC NULLS LAST) AS row_number FROM "
        + table_names[source_id]
        + " src), top_rows AS (SELECT dimension_value, metric_value FROM ranked WHERE row_number <= "
        + str(top_n)
        + "), other_row AS (SELECT 'OTHER' AS dimension_value, SUM(metric_value) AS metric_value FROM ranked WHERE row_number > "
        + str(top_n)
        + ") SELECT dimension_value, metric_value FROM top_rows UNION ALL SELECT dimension_value, metric_value FROM other_row WHERE metric_value IS NOT NULL"
    )


def _compile_pivot(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
) -> str:
    _require_inputs(task, 1)
    source_id = task.inputs[0]
    schema = schemas[source_id]
    index_columns = _option_columns(task, "index", schema)
    if not index_columns:
        index_columns = _option_columns(task, "dimensions", schema)
    pivot_column = task.options.get("columns") or task.options.get("column")
    if isinstance(pivot_column, list):
        pivot_column = pivot_column[0] if len(pivot_column) == 1 else None
    if not isinstance(pivot_column, str) or pivot_column not in schema:
        raise ComputeOperationError("COMPUTE_PIVOT_COLUMN_REQUIRED")
    metrics = _metrics(task, schema, schema, tuple(index_columns) + (pivot_column,))
    if len(metrics) != 1:
        raise ComputeOperationError("COMPUTE_PIVOT_VALUE_REQUIRED")
    metric = metrics[0]
    if not index_columns:
        raise ComputeOperationError("COMPUTE_PIVOT_INDEX_REQUIRED")
    index_sql = ", ".join(_quote(field) for field in index_columns)
    return (
        f"PIVOT {table_names[source_id]} ON {_quote(pivot_column)} "
        f"USING SUM({_quote(metric)}) GROUP BY {index_sql}"
    )


def _compile_expr(
    task: ComputeTask,
    table_names: Mapping[str, str],
    schemas: Mapping[str, tuple[str, ...]],
) -> str:
    _require_inputs(task, 1)
    source_id = task.inputs[0]
    if not task.derive:
        raise ComputeOperationError("COMPUTE_EXPR_DERIVATION_REQUIRED")
    if len(task.inputs) == 1:
        schema = schemas[source_id]
        expressions = [f"src.{_quote(field)} AS {_quote(field)}" for field in schema]
        qualifiers = None
        from_sql = f"{table_names[source_id]} src"
    else:
        keys = _require_join_columns(task, schemas)
        qualifiers = {
            input_id: schemas[input_id]
            for input_id in task.inputs
        }
        expressions = [
            "COALESCE("
            + ", ".join(
                f"{_quote(input_id)}.{_quote(key)}" for input_id in task.inputs
            )
            + f") AS {_quote(key)}"
            for key in keys
        ]
        from_sql = table_names[task.inputs[0]] + " " + _quote(task.inputs[0])
        for index, input_id in enumerate(task.inputs[1:], start=1):
            previous = task.inputs[index - 1]
            join = " AND ".join(
                f"{_quote(previous)}.{_quote(key)} IS NOT DISTINCT FROM "
                f"{_quote(input_id)}.{_quote(key)}"
                for key in keys
            )
            from_sql += (
                f" FULL OUTER JOIN {table_names[input_id]} {_quote(input_id)}"
                f" ON {join}"
            )
    for derivation in task.derive:
        validated = validate_expression(
            derivation.expr,
            schemas[source_id],
            allowed_qualifiers=qualifiers,
        )
        expressions.append(f"{validated} AS {_quote(derivation.name)}")
    return f"SELECT {', '.join(expressions)} FROM {from_sql}"


def _require_join_columns(
    task: ComputeTask,
    schemas: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    if not task.join_on:
        raise ComputeOperationError("COMPUTE_EXPR_JOIN_ON_REQUIRED")
    first_schema = schemas[task.inputs[0]]
    for input_id in task.inputs[1:]:
        if any(key not in first_schema or key not in schemas[input_id] for key in task.join_on):
            raise ComputeOperationError("COMPUTE_EXPR_JOIN_COLUMN_UNKNOWN")
    return tuple(task.join_on)


def _require_inputs(task: ComputeTask, minimum: int) -> None:
    if len(task.inputs) < minimum:
        raise ComputeOperationError("COMPUTE_INPUTS_INSUFFICIENT")


def _require_columns(
    requested: Sequence[Any],
    left_schema: tuple[str, ...],
    right_schema: tuple[str, ...],
    option_name: str,
) -> tuple[str, ...]:
    if not requested or not all(isinstance(item, str) and item for item in requested):
        raise ComputeOperationError(f"COMPUTE_{option_name.upper()}_REQUIRED")
    columns = tuple(str(item) for item in requested)
    if any(item not in left_schema or item not in right_schema for item in columns):
        raise ComputeOperationError(f"COMPUTE_{option_name.upper()}_COLUMN_UNKNOWN")
    return columns


def _option_columns(
    task: ComputeTask,
    option_name: str,
    schema: tuple[str, ...],
) -> tuple[str, ...]:
    raw = task.options.get(option_name)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    columns = tuple(str(item) for item in raw if isinstance(item, str))
    if len(columns) != len(raw) or any(item not in schema for item in columns):
        raise ComputeOperationError(f"COMPUTE_{option_name.upper()}_COLUMN_UNKNOWN")
    return columns


def _metrics(
    task: ComputeTask,
    left_schema: tuple[str, ...],
    right_schema: tuple[str, ...],
    excluded: Sequence[str],
) -> tuple[str, ...]:
    raw = task.options.get("value_columns", task.options.get("metrics"))
    if isinstance(raw, str):
        raw = [raw]
    if raw is None:
        common = [field for field in left_schema if field in right_schema]
        return tuple(field for field in common if field not in excluded)
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise ComputeOperationError("COMPUTE_VALUE_COLUMNS_INVALID")
    metrics = tuple(str(item) for item in raw)
    if any(item not in left_schema or item not in right_schema for item in metrics):
        raise ComputeOperationError("COMPUTE_VALUE_COLUMN_UNKNOWN")
    if any(item in excluded for item in metrics):
        raise ComputeOperationError("COMPUTE_VALUE_COLUMN_IS_KEY")
    return metrics


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


__all__ = ["compile_operation"]
