from __future__ import annotations

from sqlmodel import Session

from apps.datasource import PhysicalField, PhysicalTable
from apps.datasource.composition import (
    build_datasource_connection_service,
    build_datasource_metadata_service,
)
from apps.semantic.errors import SemanticDataAccessError
from apps.semantic.models.dto import SemanticColumnMeta, SemanticTableMeta


class DatasourceMetadataDiscoveryError(SemanticDataAccessError):
    """数据源实时元数据发现失败。"""

    def __init__(self, code: str, cause: Exception):
        self.code = code
        self.cause = cause
        super().__init__(f"{code}: {cause}")


def discover_datasource_tables(
    session: Session, datasource_id: int
) -> list[SemanticTableMeta]:
    persisted_schema = build_datasource_metadata_service(session).get_schema(
        datasource_id
    )
    if persisted_schema:
        return table_metas_from_persisted_tables(
            [item.table for item in persisted_schema]
        )

    try:
        live_tables = build_datasource_connection_service(session).list_tables(
            datasource_id
        )
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
    persisted_schema = build_datasource_metadata_service(session).get_schema(
        datasource_id
    )
    detail = next(
        (
            item
            for item in persisted_schema
            if item.table.table_name == table_name
        ),
        None,
    )
    if detail is not None:
        if not detail.table.checked:
            return []
        return column_metas_from_persisted_fields(detail.fields)

    try:
        live_fields = build_datasource_connection_service(session).list_fields(
            datasource_id,
            table_name,
        )
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
    tables: list[PhysicalTable],
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
    fields: list[PhysicalField],
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
