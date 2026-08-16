"""基于 DuckDB 的命名结果集确定性计算引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from apps.chatbi.models.dto.analysis_plan import ComputeTask, ResultSetSnapshot
from apps.chatbi.services.computation.errors import ComputeEngineError
from apps.chatbi.services.computation.ops import compile_operation


@dataclass(frozen=True, slots=True)
class ComputeExecution:
    """一次计算的 SQL、结果字段和结果行。"""

    sql: str
    fields: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ComputeEngine:
    """在进程内 DuckDB 会话中注册结果集并执行白名单操作。"""

    def execute(
        self,
        task: ComputeTask,
        inputs: dict[str, ResultSetSnapshot],
    ) -> ComputeExecution:
        self._validate_inputs(task, inputs)
        connection = duckdb.connect(database=":memory:")
        try:
            table_names: dict[str, str] = {}
            schemas: dict[str, tuple[str, ...]] = {}
            for index, input_id in enumerate(task.inputs):
                snapshot = inputs[input_id]
                table_name = f"input_{index}"
                table_names[input_id] = table_name
                schemas[input_id] = tuple(snapshot.ref.fields)
                self._register_snapshot(connection, table_name, snapshot)
            sql = compile_operation(task, table_names, schemas)
            if task.operation.value in {"share", "topn_other", "pivot", "expr"} and all(
                not inputs[input_id].rows for input_id in task.inputs
            ):
                return ComputeExecution(
                    sql=sql,
                    fields=self._empty_output_fields(task, schemas),
                    rows=(),
                )
            try:
                cursor = connection.execute(sql)
                fields = tuple(str(item[0]) for item in (cursor.description or ()))
                values = cursor.fetchall()
            except Exception as exc:
                raise ComputeEngineError("COMPUTE_SQL_EXECUTION_FAILED") from exc
            rows = tuple(
                {
                    field: _normalize_value(value)
                    for field, value in zip(fields, row, strict=True)
                }
                for row in values
            )
            return ComputeExecution(sql=sql, fields=fields, rows=rows)
        finally:
            connection.close()

    @staticmethod
    def _validate_inputs(task: ComputeTask, inputs: dict[str, ResultSetSnapshot]) -> None:
        if not task.inputs:
            raise ComputeEngineError("COMPUTE_INPUTS_REQUIRED")
        if len(set(task.inputs)) != len(task.inputs):
            raise ComputeEngineError("COMPUTE_INPUTS_DUPLICATED")
        missing = [item for item in task.inputs if item not in inputs]
        if missing:
            raise ComputeEngineError("COMPUTE_INPUT_RESULT_SET_MISSING")

    @staticmethod
    def _register_snapshot(
        connection: duckdb.DuckDBPyConnection,
        table_name: str,
        snapshot: ResultSetSnapshot,
    ) -> None:
        fields = tuple(snapshot.ref.fields)
        if not fields:
            raise ComputeEngineError("COMPUTE_INPUT_FIELDS_REQUIRED")
        if len(fields) != len(set(fields)):
            raise ComputeEngineError("COMPUTE_INPUT_FIELDS_DUPLICATED")
        columns = ", ".join(
            f"{_quote(field)} {_duckdb_type(snapshot.rows, field)}" for field in fields
        )
        connection.execute(f"CREATE TABLE {_quote(table_name)} ({columns})")
        if not snapshot.rows:
            return
        placeholders = ", ".join("?" for _ in fields)
        values = [tuple(row.get(field) for field in fields) for row in snapshot.rows]
        try:
            connection.executemany(
                f"INSERT INTO {_quote(table_name)} VALUES ({placeholders})",
                values,
            )
        except Exception as exc:
            raise ComputeEngineError("COMPUTE_INPUT_RESULT_SET_INVALID") from exc

    @staticmethod
    def _empty_output_fields(
        task: ComputeTask,
        schemas: dict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        schema = schemas[task.inputs[0]]
        if task.operation.value == "share":
            metrics = task.options.get("value_columns", task.options.get("metrics"))
            metric = metrics[0] if isinstance(metrics, list) and metrics else metrics
            return (*schema, f"{metric}_share") if isinstance(metric, str) else schema
        if task.operation.value == "topn_other":
            return ("dimension_value", "metric_value")
        if task.operation.value == "pivot":
            index = task.options.get("index", task.options.get("dimensions"))
            if isinstance(index, str):
                return (index,)
            if isinstance(index, list):
                return tuple(str(item) for item in index if isinstance(item, str))
            return ()
        return (*schema, *(item.name for item in task.derive))


def _duckdb_type(rows: tuple[dict[str, Any], ...], field: str) -> str:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if not values:
        return "VARCHAR"
    value_types = {type(value) for value in values}
    if value_types <= {bool}:
        return "BOOLEAN"
    if value_types <= {int}:
        return "BIGINT"
    if value_types <= {int, float, Decimal}:
        return "DOUBLE"
    if value_types <= {date}:
        return "DATE"
    if value_types <= {datetime}:
        return "TIMESTAMP"
    if value_types <= {str}:
        return "VARCHAR"
    return "VARCHAR"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


__all__ = ["ComputeEngine", "ComputeExecution"]
