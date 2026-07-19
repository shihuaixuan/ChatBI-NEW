from sqlalchemy import delete
from sqlmodel import Session, col, select

from apps.datasource.models.dto import (
    PhysicalField,
    PhysicalTable,
    PhysicalTableSnapshot,
)
from apps.datasource.models.orm import CoreDatasource, CoreField, CoreTable


class SQLModelDatasourceMetadataRepository:
    """物理元数据快照的 SQLModel 仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_tables(self, datasource_id: int) -> list[PhysicalTable]:
        rows = self._session.exec(
            select(CoreTable)
            .where(col(CoreTable.ds_id) == datasource_id)
            .order_by(col(CoreTable.table_name))
        ).all()
        return [self._table_dto(row) for row in rows]

    def list_fields(
        self,
        table_id: int,
        keyword: str | None = None,
    ) -> list[PhysicalField]:
        statement = select(CoreField).where(col(CoreField.table_id) == table_id)
        if keyword:
            statement = statement.where(col(CoreField.field_name).ilike(f"%{keyword}%"))
        rows = self._session.exec(statement.order_by(col(CoreField.field_index))).all()
        return [self._field_dto(row) for row in rows]

    def get_table(self, table_id: int) -> PhysicalTable | None:
        row = self._session.get(CoreTable, table_id)
        return self._table_dto(row) if row is not None else None

    def replace_schema(
        self,
        datasource_id: int,
        snapshots: list[PhysicalTableSnapshot],
        total_table_count: int,
    ) -> None:
        datasource = self._session.get(CoreDatasource, datasource_id)
        if datasource is None:
            raise ValueError(f"Datasource {datasource_id} not found")

        try:
            existing_tables = {
                row.table_name: row
                for row in self._session.exec(
                    select(CoreTable).where(col(CoreTable.ds_id) == datasource_id)
                ).all()
            }
            retained_table_ids: list[int] = []
            for snapshot in snapshots:
                table = existing_tables.get(snapshot.table_name)
                if table is None:
                    table = CoreTable(
                        ds_id=datasource_id,
                        checked=True,
                        table_name=snapshot.table_name,
                        table_comment=snapshot.table_comment or "",
                        custom_comment=snapshot.table_comment or "",
                    )
                    self._session.add(table)
                    self._session.flush()
                else:
                    table.table_comment = snapshot.table_comment or ""
                    self._session.add(table)
                retained_table_ids.append(table.id)
                self._replace_fields_without_commit(
                    datasource_id,
                    table.id,
                    snapshot.fields,
                )

            if retained_table_ids:
                self._session.exec(
                    delete(CoreField).where(
                        col(CoreField.ds_id) == datasource_id,
                        col(CoreField.table_id).not_in(retained_table_ids),
                    )
                )
                self._session.exec(
                    delete(CoreTable).where(
                        col(CoreTable.ds_id) == datasource_id,
                        col(CoreTable.id).not_in(retained_table_ids),
                    )
                )
            else:
                self._session.exec(
                    delete(CoreField).where(col(CoreField.ds_id) == datasource_id)
                )
                self._session.exec(
                    delete(CoreTable).where(col(CoreTable.ds_id) == datasource_id)
                )

            datasource.num = f"{len(snapshots)}/{total_table_count}"
            self._session.add(datasource)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def replace_fields(
        self,
        datasource_id: int,
        table_id: int,
        fields: list[PhysicalField],
    ) -> None:
        try:
            self._replace_fields_without_commit(datasource_id, table_id, fields)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def update_table(self, table: PhysicalTable) -> None:
        row = self._require_table(table.id)
        try:
            row.checked = table.checked
            row.custom_comment = table.custom_comment or ""
            self._session.add(row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def update_field(self, field: PhysicalField) -> None:
        row = self._require_field(field.id)
        try:
            row.checked = field.checked
            row.custom_comment = field.custom_comment or ""
            self._session.add(row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def update_table_and_fields(
        self,
        table: PhysicalTable,
        fields: list[PhysicalField],
    ) -> None:
        table_row = self._require_table(table.id)
        field_rows = [self._require_field(field.id) for field in fields]
        try:
            table_row.checked = table.checked
            table_row.custom_comment = table.custom_comment or ""
            self._session.add(table_row)
            for field, row in zip(fields, field_rows, strict=True):
                if row.table_id != table_row.id:
                    raise ValueError(
                        f"Field {row.id} does not belong to table {table_row.id}"
                    )
                row.checked = field.checked
                row.custom_comment = field.custom_comment or ""
                self._session.add(row)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def _replace_fields_without_commit(
        self,
        datasource_id: int,
        table_id: int,
        fields: list[PhysicalField],
    ) -> None:
        existing_fields = {
            row.field_name: row
            for row in self._session.exec(
                select(CoreField).where(col(CoreField.table_id) == table_id)
            ).all()
        }
        retained_field_ids: list[int] = []
        for index, field in enumerate(fields):
            row = existing_fields.get(field.field_name)
            if row is None:
                row = CoreField(
                    ds_id=datasource_id,
                    table_id=table_id,
                    checked=True,
                    field_name=field.field_name,
                    field_type=field.field_type,
                    field_comment=field.field_comment or "",
                    custom_comment=field.field_comment or "",
                    field_index=index,
                )
                self._session.add(row)
                self._session.flush()
            else:
                row.field_type = field.field_type or ""
                row.field_comment = field.field_comment or ""
                row.field_index = index
                self._session.add(row)
            retained_field_ids.append(row.id)

        if retained_field_ids:
            self._session.exec(
                delete(CoreField).where(
                    col(CoreField.table_id) == table_id,
                    col(CoreField.id).not_in(retained_field_ids),
                )
            )
        else:
            self._session.exec(
                delete(CoreField).where(col(CoreField.table_id) == table_id)
            )

    def _require_table(self, table_id: int | None) -> CoreTable:
        row = self._session.get(CoreTable, table_id) if table_id is not None else None
        if row is None:
            raise ValueError(f"Table {table_id} not found")
        return row

    def _require_field(self, field_id: int | None) -> CoreField:
        row = self._session.get(CoreField, field_id) if field_id is not None else None
        if row is None:
            raise ValueError(f"Field {field_id} not found")
        return row

    @staticmethod
    def _table_dto(row: CoreTable) -> PhysicalTable:
        return PhysicalTable.model_validate(row, from_attributes=True)

    @staticmethod
    def _field_dto(row: CoreField) -> PhysicalField:
        return PhysicalField.model_validate(row, from_attributes=True)
