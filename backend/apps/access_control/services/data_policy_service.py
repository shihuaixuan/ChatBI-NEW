"""统一解析行权限、列权限和变量的数据策略 Service。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Final, Literal, cast

from apps.access_control.errors import (
    DataPolicyConfigurationError,
    DataPolicyDatasourceNotFoundError,
    UserVariableAssignmentError,
)
from apps.access_control.models.dto import (
    AccessVariableRecord,
    AccessVariableValue,
    DataPolicy,
    DataPolicyDeniedColumn,
    DataPolicyExpression,
    DataPolicyPredicate,
    DataPolicyRowFilter,
    DataPolicySubject,
    StoredDataRule,
    UserVariableAssignment,
)
from apps.access_control.repository import DataPolicyRepository
from apps.access_control.services.access_variable_service import (
    SYSTEM_VARIABLE_TYPE,
    AccessVariableService,
)
from apps.datasource import (
    DatasourcePolicyCatalog,
    DatasourcePolicyField,
    DatasourcePolicySchema,
    DatasourcePolicyTable,
)

SQL_OPERATORS: Final = {
    "eq": "=",
    "not_eq": "<>",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "in": "IN",
    "not in": "NOT IN",
    "like": "LIKE",
    "not like": "NOT LIKE",
    "null": "IS NULL",
    "not_null": "IS NOT NULL",
    "empty": "=",
    "not_empty": "<>",
}
VALUELESS_TERMS: Final = frozenset({"null", "not_null", "empty", "not_empty"})
SYSTEM_SUBJECT_ATTRIBUTES: Final = {
    "name": "name",
    "account": "account",
    "email": "email",
}


class DataPolicyService:
    def __init__(
        self,
        repository: DataPolicyRepository,
        datasource_catalog: DatasourcePolicyCatalog,
        variable_service: AccessVariableService,
    ) -> None:
        self._repository = repository
        self._datasource_catalog = datasource_catalog
        self._variable_service = variable_service

    def resolve(
        self,
        subject: DataPolicySubject,
        datasource_id: int,
        *,
        table_names: list[str] | None = None,
        table_id: int | None = None,
    ) -> DataPolicy:
        schema = self._datasource_catalog.get_policy_schema(
            subject.workspace_id,
            datasource_id,
            table_names=table_names,
            table_id=table_id,
        )
        if schema is None:
            raise DataPolicyDatasourceNotFoundError(
                datasource_id,
                subject.workspace_id,
            )
        if table_id is not None and not schema.tables:
            raise DataPolicyConfigurationError(f"TABLE_NOT_FOUND:{table_id}")
        if table_names is not None:
            found_names = {table.name for table in schema.tables}
            missing_names = sorted(set(table_names) - found_names)
            if missing_names:
                raise DataPolicyConfigurationError(
                    f"TABLE_NOT_FOUND:{missing_names[0]}"
                )
        authorized_tables = [table.name for table in schema.tables]
        if subject.is_system_admin:
            return DataPolicy(authorized_tables=authorized_tables)
        if not schema.tables:
            return DataPolicy(authorized_tables=[])

        permissions = self._repository.list_permissions(
            datasource_id,
            {table.id for table in schema.tables},
        )
        applicable_ids = self._applicable_permission_ids(
            self._repository.list_rules(subject.workspace_id),
            subject.user_id,
        )
        applicable_permissions = [
            permission
            for permission in permissions
            if permission.id in applicable_ids
            and not self._contains_user(
                permission.white_list_user,
                subject.user_id,
                required=False,
                field=f"PERMISSION_WHITE_LIST:{permission.id}",
            )
        ]

        table_by_id = {table.id: table for table in schema.tables}
        variable_cache: dict[int, AccessVariableRecord] = {}
        assignment_by_id = {
            assignment.variable_id: assignment
            for assignment in subject.variable_assignments
        }
        row_expressions: dict[int, list[tuple[DataPolicyExpression, str]]] = (
            defaultdict(list)
        )
        denied_columns: dict[tuple[int, int], DataPolicyDeniedColumn] = {}

        for permission in applicable_permissions:
            table = table_by_id.get(permission.table_id)
            if table is None:
                raise DataPolicyConfigurationError(
                    f"TABLE_OUT_OF_SCOPE:{permission.id}:{permission.table_id}"
                )
            if permission.permission_type == "row":
                raw_tree = self._json_object(
                    permission.expression_tree,
                    f"ROW_EXPRESSION:{permission.id}",
                )
                expression, condition = self._resolve_expression(
                    raw_tree,
                    schema,
                    table,
                    subject,
                    assignment_by_id,
                    variable_cache,
                )
                row_expressions[table.id].append((expression, condition))
            elif permission.permission_type == "column":
                raw_items = self._json_list(
                    permission.permissions,
                    f"COLUMN_PERMISSION:{permission.id}",
                    required=True,
                )
                field_by_id = {field.id: field for field in table.fields}
                for raw_item in raw_items:
                    if not isinstance(raw_item, dict):
                        raise DataPolicyConfigurationError(
                            f"COLUMN_ITEM:{permission.id}"
                        )
                    field_id = self._positive_int(
                        raw_item.get("field_id"),
                        f"COLUMN_FIELD:{permission.id}",
                    )
                    field = field_by_id.get(field_id)
                    if field is None:
                        raise DataPolicyConfigurationError(
                            f"COLUMN_FIELD_OUT_OF_SCOPE:{permission.id}:{field_id}"
                        )
                    enabled = raw_item.get("enable")
                    if not isinstance(enabled, bool):
                        raise DataPolicyConfigurationError(
                            f"COLUMN_ENABLE:{permission.id}:{field_id}"
                        )
                    if not enabled:
                        denied_columns[(table.id, field.id)] = DataPolicyDeniedColumn(
                            table_id=table.id,
                            table=table.name,
                            field_id=field.id,
                            column=field.name,
                        )
            else:
                raise DataPolicyConfigurationError(
                    f"PERMISSION_TYPE:{permission.permission_type}"
                )

        return DataPolicy(
            authorized_tables=authorized_tables,
            row_filters=[
                DataPolicyRowFilter(
                    table_id=table_id_value,
                    table=table_by_id[table_id_value].name,
                    expressions=[item[0] for item in items],
                    condition=" AND ".join(f"({item[1]})" for item in items),
                )
                for table_id_value, items in row_expressions.items()
            ],
            denied_columns=list(denied_columns.values()),
        )

    def _applicable_permission_ids(
        self,
        rules: list[StoredDataRule],
        user_id: int,
    ) -> set[int]:
        permission_ids: set[int] = set()
        for rule in rules:
            if self._contains_user(
                rule.white_list_user,
                user_id,
                required=False,
                field=f"RULE_WHITE_LIST:{rule.id}",
            ):
                continue
            if not self._contains_user(
                rule.user_list,
                user_id,
                required=True,
                field=f"RULE_USERS:{rule.id}",
            ):
                continue
            permission_ids.update(
                self._id_set(
                    rule.permission_list,
                    f"RULE_PERMISSIONS:{rule.id}",
                    required=True,
                )
            )
        return permission_ids

    def _resolve_expression(
        self,
        raw_tree: dict[str, Any],
        schema: DatasourcePolicySchema,
        table: DatasourcePolicyTable,
        subject: DataPolicySubject,
        assignment_by_id: dict[int, UserVariableAssignment],
        variable_cache: dict[int, AccessVariableRecord],
    ) -> tuple[DataPolicyExpression, str]:
        logic_value = raw_tree.get("logic")
        if not isinstance(logic_value, str) or logic_value.lower() not in {"and", "or"}:
            raise DataPolicyConfigurationError("ROW_LOGIC")
        logic = cast(Literal["and", "or"], logic_value.lower())
        raw_items = raw_tree.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise DataPolicyConfigurationError("ROW_ITEMS_REQUIRED")

        expression_items: list[DataPolicyPredicate | DataPolicyExpression] = []
        conditions: list[str] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise DataPolicyConfigurationError("ROW_ITEM")
            item_type = raw_item.get("type")
            item: DataPolicyPredicate | DataPolicyExpression
            if item_type == "tree":
                raw_subtree = raw_item.get("sub_tree")
                if not isinstance(raw_subtree, dict):
                    raise DataPolicyConfigurationError("ROW_SUBTREE")
                item, condition = self._resolve_expression(
                    raw_subtree,
                    schema,
                    table,
                    subject,
                    assignment_by_id,
                    variable_cache,
                )
            elif item_type == "item":
                item, condition = self._resolve_predicate(
                    raw_item,
                    schema,
                    table,
                    subject,
                    assignment_by_id,
                    variable_cache,
                )
            else:
                raise DataPolicyConfigurationError(f"ROW_ITEM_TYPE:{item_type}")
            expression_items.append(item)
            conditions.append(condition)

        sql_logic = logic.upper()
        return (
            DataPolicyExpression(logic=logic, items=expression_items),
            f" {sql_logic} ".join(f"({condition})" for condition in conditions),
        )

    def _resolve_predicate(
        self,
        raw_item: dict[str, Any],
        schema: DatasourcePolicySchema,
        table: DatasourcePolicyTable,
        subject: DataPolicySubject,
        assignment_by_id: dict[int, UserVariableAssignment],
        variable_cache: dict[int, AccessVariableRecord],
    ) -> tuple[DataPolicyPredicate, str]:
        field_id = self._positive_int(raw_item.get("field_id"), "ROW_FIELD")
        field = next((item for item in table.fields if item.id == field_id), None)
        if field is None:
            raise DataPolicyConfigurationError(f"ROW_FIELD_OUT_OF_SCOPE:{field_id}")
        term = raw_item.get("term")
        if not isinstance(term, str) or term not in SQL_OPERATORS:
            raise DataPolicyConfigurationError(f"ROW_TERM:{term}")
        filter_type = raw_item.get("filter_type")
        if filter_type not in {"logic", "enum"}:
            raise DataPolicyConfigurationError(f"ROW_FILTER_TYPE:{filter_type}")

        values = self._resolve_values(
            raw_item,
            term,
            subject,
            assignment_by_id,
            variable_cache,
        )
        predicate = DataPolicyPredicate(
            field_id=field.id,
            field_name=field.name,
            operator=term,
            values=values,
        )
        return predicate, self._render_predicate(schema, field, term, values)

    def _resolve_values(
        self,
        raw_item: dict[str, Any],
        term: str,
        subject: DataPolicySubject,
        assignment_by_id: dict[int, UserVariableAssignment],
        variable_cache: dict[int, AccessVariableRecord],
    ) -> list[AccessVariableValue]:
        if term in VALUELESS_TERMS:
            return []
        if raw_item.get("filter_type") == "enum":
            raw_values = raw_item.get("enum_value")
            if not isinstance(raw_values, list) or not raw_values:
                raise DataPolicyConfigurationError("ROW_ENUM_VALUES")
            if any(not isinstance(value, str) for value in raw_values):
                raise DataPolicyConfigurationError("ROW_ENUM_VALUE_TYPE")
            return raw_values
        value_type = raw_item.get("value_type", "normal")
        if value_type not in {"normal", "variable"}:
            raise DataPolicyConfigurationError(f"ROW_VALUE_TYPE:{value_type}")
        if value_type == "variable":
            variable_id = self._positive_int(
                raw_item.get("variable_id"),
                "ROW_VARIABLE_ID",
            )
            variable = variable_cache.get(variable_id)
            if variable is None:
                variable = self._variable_service.get(variable_id)
                variable_cache[variable_id] = variable
            if variable.type == SYSTEM_VARIABLE_TYPE:
                return [self._system_variable_value(variable, subject)]
            assignment = assignment_by_id.get(variable_id)
            if assignment is None:
                raise UserVariableAssignmentError(f"MISSING:{variable_id}")
            return self._variable_service.normalize_assignment(
                variable,
                assignment,
            ).variable_values

        raw_value = raw_item.get("value")
        if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
            raise DataPolicyConfigurationError("ROW_VALUE")
        if term in {"in", "not in"}:
            if not isinstance(raw_value, str):
                raise DataPolicyConfigurationError("ROW_LIST_VALUE")
            values: list[AccessVariableValue] = [
                value.strip() for value in raw_value.split(",") if value.strip()
            ]
            if not values:
                raise DataPolicyConfigurationError("ROW_LIST_VALUE")
            return values
        return [raw_value]

    @staticmethod
    def _system_variable_value(
        variable: AccessVariableRecord,
        subject: DataPolicySubject,
    ) -> str:
        if len(variable.value) != 1 or not isinstance(variable.value[0], str):
            raise DataPolicyConfigurationError(f"SYSTEM_VARIABLE:{variable.id}")
        attribute = SYSTEM_SUBJECT_ATTRIBUTES.get(variable.value[0])
        if attribute is None:
            raise DataPolicyConfigurationError(f"SYSTEM_VARIABLE:{variable.id}")
        return str(getattr(subject, attribute))

    def _render_predicate(
        self,
        schema: DatasourcePolicySchema,
        field: DatasourcePolicyField,
        term: str,
        values: list[AccessVariableValue],
    ) -> str:
        identifier = self._quote_identifier(schema, field.name)
        operator = SQL_OPERATORS[term]
        if term in {"null", "not_null"}:
            return f"{identifier} {operator}"
        if term in {"empty", "not_empty"}:
            return f"{identifier} {operator} ''"
        national = schema.database_type == "sqlServer" and (
            (field.data_type or "").lower() in {"nchar", "nvarchar"}
        )
        rendered = [self._sql_literal(value, national) for value in values]
        if term in {"in", "not in"}:
            return f"{identifier} {operator} ({', '.join(rendered)})"
        if term in {"like", "not like"}:
            value = str(values[0]).replace("'", "''")
            prefix = "N" if national else ""
            return f"{identifier} {operator} {prefix}'%{value}%'"
        return f"{identifier} {operator} {rendered[0]}"

    @staticmethod
    def _sql_literal(value: AccessVariableValue, national: bool) -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        escaped = str(value).replace("'", "''")
        return f"{'N' if national else ''}'{escaped}'"

    @staticmethod
    def _quote_identifier(schema: DatasourcePolicySchema, name: str) -> str:
        suffix = schema.identifier_suffix
        escaped = name.replace(suffix, suffix * 2) if suffix else name
        return f"{schema.identifier_prefix}{escaped}{suffix}"

    def _contains_user(
        self,
        raw_value: str | None,
        user_id: int,
        *,
        required: bool,
        field: str,
    ) -> bool:
        values = self._json_list(raw_value, field, required=required)
        return any(str(value) == str(user_id) for value in values)

    def _id_set(
        self,
        raw_value: str | None,
        field: str,
        *,
        required: bool,
    ) -> set[int]:
        values = self._json_list(raw_value, field, required=required)
        return {self._positive_int(value, field) for value in values}

    @staticmethod
    def _json_list(
        raw_value: str | None,
        field: str,
        *,
        required: bool,
    ) -> list[Any]:
        if raw_value is None or raw_value == "":
            if required:
                raise DataPolicyConfigurationError(f"{field}:REQUIRED")
            return []
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise DataPolicyConfigurationError(f"{field}:JSON") from exc
        if not isinstance(value, list):
            raise DataPolicyConfigurationError(f"{field}:LIST")
        return value

    @staticmethod
    def _json_object(raw_value: str | None, field: str) -> dict[str, Any]:
        if not raw_value:
            raise DataPolicyConfigurationError(f"{field}:REQUIRED")
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise DataPolicyConfigurationError(f"{field}:JSON") from exc
        if not isinstance(value, dict):
            raise DataPolicyConfigurationError(f"{field}:OBJECT")
        return value

    @staticmethod
    def _positive_int(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise DataPolicyConfigurationError(f"{field}:INTEGER")
        try:
            normalized = int(value)
        except ValueError as exc:
            raise DataPolicyConfigurationError(f"{field}:INTEGER") from exc
        if normalized <= 0:
            raise DataPolicyConfigurationError(f"{field}:POSITIVE")
        return normalized
