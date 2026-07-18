from __future__ import annotations

from sqlalchemy import select
from sqlmodel import Session

from apps.datasource.models.datasource import CoreDatasource, CoreField, CoreTable
from apps.semantic.errors import SemanticDataAccessError
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta
from apps.semantic.repository.sqlmodel.results import all_results, first_result


class DatasourceMetadataDiscoveryError(SemanticDataAccessError):
    """数据源实时元数据发现失败。"""

    def __init__(self, code: str, cause: Exception):
        self.code = code
        self.cause = cause
        super().__init__(f"{code}: {cause}")


def discover_datasource_tables(
    session: Session, datasource_id: int
) -> list[SemanticTableMeta]:
    persisted_tables = all_results(
        session.exec(
            select(CoreTable)
            .where(CoreTable.ds_id == datasource_id)
            .order_by(CoreTable.table_name)
        )
    )
    if persisted_tables:
        return table_metas_from_persisted_tables(persisted_tables)

    try:
        from apps.db.db import get_tables

        datasource = session.get(CoreDatasource, datasource_id)
        live_tables = get_tables(datasource)
        return [
            SemanticTableMeta(
                table_name=item.tableName,
                table_comment=item.tableComment,
            )
            for item in live_tables
        ]
    except Exception as exc:
        raise DatasourceMetadataDiscoveryError(
            "SEMANTIC_TABLE_DISCOVERY_FAILED", exc
        ) from exc


def discover_datasource_columns(
    session: Session, datasource_id: int, table_name: str
) -> list[SemanticColumnMeta]:
    table = first_result(
        session.exec(
            select(CoreTable).where(
                CoreTable.ds_id == datasource_id, CoreTable.table_name == table_name
            )
        )
    )
    if table is not None:
        if not table.checked:
            return []
        persisted_fields = all_results(
            session.exec(
                select(CoreField)
                .where(CoreField.table_id == table.id)
                .order_by(CoreField.field_index)
            )
        )
        return column_metas_from_persisted_fields(persisted_fields)

    try:
        from apps.db.db import get_fields

        datasource = session.get(CoreDatasource, datasource_id)
        live_fields = get_fields(datasource, table_name)
        return [
            SemanticColumnMeta(
                field_name=item.fieldName,
                field_type=item.fieldType,
                field_comment=item.fieldComment,
                field_index=index,
            )
            for index, item in enumerate(live_fields)
        ]
    except Exception as exc:
        raise DatasourceMetadataDiscoveryError(
            "SEMANTIC_COLUMN_DISCOVERY_FAILED", exc
        ) from exc


def table_metas_from_persisted_tables(
    tables: list[CoreTable],
) -> list[SemanticTableMeta]:
    # 新建模型必须遵守数据源配置时勾选的表范围，避免实时读库绕过表白名单。
    return [
        SemanticTableMeta(
            id=item.id,
            table_name=item.table_name,
            table_comment=_normalize_comment_text(
                item.custom_comment or item.table_comment
            ),
            checked=item.checked,
        )
        for item in tables
        if item.checked
    ]


def column_metas_from_persisted_fields(
    fields: list[CoreField],
) -> list[SemanticColumnMeta]:
    # 字段同样使用数据源已保存的中文注释，规避驱动实时读取注释时的编码不一致。
    return [
        SemanticColumnMeta(
            id=item.id,
            field_name=item.field_name,
            field_type=item.field_type,
            field_comment=_normalize_comment_text(
                item.custom_comment or item.field_comment
            ),
            field_index=item.field_index,
            checked=item.checked,
        )
        for item in fields
        if item.checked
    ]


def _normalize_comment_text(comment: str | None) -> str | None:
    if not comment:
        return comment
    raw_bytes = bytearray()
    try:
        # 兼容历史 MySQL 注释被按 latin1/CP1252 存入元数据表的情况。
        for char in comment:
            code_point = ord(char)
            if code_point <= 0xFF:
                raw_bytes.append(code_point)
            else:
                raw_bytes.extend(char.encode("cp1252"))
        return bytes(raw_bytes).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return comment
