import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
from pandas import DataFrame  # type: ignore[import-untyped]
from sqlalchemy import Engine

from apps.datasource.models.dto import (
    ExcelImportResult,
    ImportedExcelSheet,
    SheetFields,
)
from apps.datasource.repository.connectors.local_engine import get_engine_conn
from apps.datasource.utils.excel import USER_TYPE_TO_PANDAS


class PostgreSQLExcelImportGateway:
    """使用同一数据库事务导入全部 Sheet。"""

    def __init__(
        self,
        engine_factory: Callable[[], Engine] = get_engine_conn,
        table_name_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._table_name_factory = table_name_factory or self._new_table_name

    def import_file(
        self,
        file_path: Path,
        sheets: list[SheetFields],
    ) -> ExcelImportResult:
        frames = self._load_frames(file_path, sheets)
        return self._import_frames(file_path, frames)

    def import_all(self, file_path: Path) -> ExcelImportResult:
        if file_path.suffix.lower() == ".csv":
            frames = [("Sheet1", pd.read_csv(file_path, engine="c"))]
        else:
            sheet_names = pd.ExcelFile(file_path).sheet_names
            frames = [
                (
                    sheet_name,
                    pd.read_excel(
                        file_path,
                        sheet_name=sheet_name,
                        engine="calamine",
                    ),
                )
                for sheet_name in sheet_names
            ]
        return self._import_frames(file_path, frames)

    def _import_frames(
        self,
        file_path: Path,
        frames: list[tuple[str, DataFrame]],
    ) -> ExcelImportResult:
        imported: list[ImportedExcelSheet] = []
        engine = self._engine_factory()

        # PostgreSQL 的建表和写入处于同一事务，任一 Sheet 失败都会整体回滚。
        with engine.begin() as connection:
            for sheet_name, frame in frames:
                table_name = self._table_name_factory(sheet_name)
                self._normalize_unsigned_columns(frame)
                frame.to_sql(
                    table_name,
                    connection,
                    if_exists="fail",
                    index=False,
                )
                imported.append(
                    ImportedExcelSheet(
                        sheetName=sheet_name,
                        tableName=table_name,
                        rows=len(frame),
                    )
                )

        return ExcelImportResult(filename=file_path.name, sheets=imported)

    def _load_frames(
        self,
        file_path: Path,
        sheets: list[SheetFields],
    ) -> list[tuple[str, DataFrame]]:
        if file_path.suffix.lower() == ".csv":
            if len(sheets) != 1:
                raise ValueError("CSV 导入必须且只能选择一个 Sheet")
            sheet = sheets[0]
            return [
                (
                    "Sheet1",
                    pd.read_csv(
                        file_path,
                        engine="c",
                        dtype=self._dtype_mapping(sheet),
                    ),
                )
            ]

        return [
            (
                sheet.sheetName,
                pd.read_excel(
                    file_path,
                    sheet_name=sheet.sheetName,
                    engine="calamine",
                    dtype=self._dtype_mapping(sheet),
                ),
            )
            for sheet in sheets
        ]

    @staticmethod
    def _dtype_mapping(sheet: SheetFields) -> dict[str, str]:
        return {
            str(field.fieldName): USER_TYPE_TO_PANDAS.get(field.fieldType, "string")
            for field in sheet.fields
        }

    @staticmethod
    def _normalize_unsigned_columns(frame: DataFrame) -> None:
        for column, dtype in frame.dtypes.items():
            if str(dtype) == "uint64":
                frame[str(column)] = frame[str(column)].astype("string")

    @staticmethod
    def _new_table_name(sheet_name: str) -> str:
        suffix = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:10]
        return f"{sheet_name}_{suffix}"
