"""数据策略所需物理表字段目录。"""

from collections.abc import Callable
from typing import Protocol, cast

from sqlmodel import Session, col, select

from apps.datasource.contracts import (
    DatasourcePolicyCatalog,
    DatasourcePolicyField,
    DatasourcePolicySchema,
    DatasourcePolicyTable,
)
from apps.datasource.models.datasource import CoreDatasource, CoreField, CoreTable
from apps.db.constant import DB


class _IdentifierDialect(Protocol):
    """数据策略渲染 SQL 标识符所需的数据库方言信息。"""

    prefix: str
    suffix: str


class SQLModelDatasourcePolicyCatalog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_policy_schema(
        self,
        workspace_id: int,
        datasource_id: int,
        *,
        table_names: list[str] | None = None,
        table_id: int | None = None,
    ) -> DatasourcePolicySchema | None:
        datasource = self._session.exec(
            select(CoreDatasource).where(
                col(CoreDatasource.id) == datasource_id,
                col(CoreDatasource.oid) == workspace_id,
            )
        ).first()
        if datasource is None:
            return None

        table_statement = select(CoreTable).where(col(CoreTable.ds_id) == datasource_id)
        if table_names is not None and not table_names:
            tables: list[CoreTable] = []
        elif table_id is not None:
            table_statement = table_statement.where(col(CoreTable.id) == table_id)
            tables = list(self._session.exec(table_statement).all())
        else:
            if table_names is not None:
                table_statement = table_statement.where(
                    col(CoreTable.table_name).in_(table_names)
                )
            tables = list(self._session.exec(table_statement).all())

        table_ids = [table.id for table in tables if table.id is not None]
        fields = (
            list(
                self._session.exec(
                    select(CoreField).where(col(CoreField.table_id).in_(table_ids))
                ).all()
            )
            if table_ids
            else []
        )
        fields_by_table: dict[int, list[DatasourcePolicyField]] = {}
        for field in fields:
            if field.id is None:
                continue
            fields_by_table.setdefault(int(field.table_id), []).append(
                DatasourcePolicyField(
                    id=int(field.id),
                    name=field.field_name,
                    data_type=field.field_type,
                )
            )

        get_database = cast(Callable[[str], _IdentifierDialect], DB.get_db)
        database = get_database(datasource.type)
        return DatasourcePolicySchema(
            id=int(datasource.id),
            workspace_id=int(datasource.oid),
            database_type=datasource.type,
            identifier_prefix=database.prefix,
            identifier_suffix=database.suffix,
            tables=[
                DatasourcePolicyTable(
                    id=int(table.id),
                    name=table.table_name,
                    fields=fields_by_table.get(int(table.id), []),
                )
                for table in tables
                if table.id is not None
            ],
        )


def build_datasource_policy_catalog(session: Session) -> DatasourcePolicyCatalog:
    return SQLModelDatasourcePolicyCatalog(session)
