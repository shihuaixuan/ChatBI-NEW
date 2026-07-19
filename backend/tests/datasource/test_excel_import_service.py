import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from apps.datasource.models.dto import FieldInfo, ImportRequest, SheetFields
from apps.datasource.repository.connectors.excel_import import (
    PostgreSQLExcelImportGateway,
)
from apps.datasource.services import ExcelImportFileError, ExcelImportService


def _csv_request(filename: str) -> ImportRequest:
    return ImportRequest(
        filePath=filename,
        sheets=[
            SheetFields(
                sheetName="Sheet1",
                fields=[
                    FieldInfo(fieldName="id", fieldType="int"),
                    FieldInfo(fieldName="amount", fieldType="float"),
                ],
            )
        ],
    )


def test_csv_import_writes_each_row_once_and_cleans_temporary_file(tmp_path):
    upload_directory = tmp_path / "uploads"
    upload_directory.mkdir()
    csv_path = upload_directory / "sales.csv"
    csv_path.write_text("id,amount\n1,12.5\n2,18.0\n", encoding="utf-8")

    database_path = tmp_path / "imports.db"
    engine = create_engine(f"sqlite:///{database_path}")
    gateway = PostgreSQLExcelImportGateway(
        engine_factory=lambda: engine,
        table_name_factory=lambda _sheet: "sales_import",
    )
    service = ExcelImportService(gateway, upload_directory)

    result = service.import_file(_csv_request(csv_path.name))

    assert result.sheets[0].rows == 2
    assert not csv_path.exists()
    with engine.connect() as connection:
        count = connection.execute(
            text("select count(*) from sales_import")
        ).scalar_one()
    assert count == 2


class RecordingTransaction:
    def __init__(self) -> None:
        self.exception_type = None

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, _exc, _traceback):
        self.exception_type = exc_type
        return False


class RecordingEngine:
    def __init__(self) -> None:
        self.transaction = RecordingTransaction()

    def begin(self) -> RecordingTransaction:
        return self.transaction


def test_multi_sheet_failure_leaves_transaction_for_rollback_and_cleans_file(
    tmp_path,
    monkeypatch,
):
    upload_directory = tmp_path / "uploads"
    upload_directory.mkdir()
    file_path = upload_directory / "sales.xlsx"
    file_path.write_bytes(b"placeholder")
    engine = RecordingEngine()
    gateway = PostgreSQLExcelImportGateway(engine_factory=lambda: engine)
    monkeypatch.setattr(
        gateway,
        "_load_frames",
        lambda _path, _sheets: [
            ("Sheet1", pd.DataFrame({"id": [1]})),
            ("Sheet2", pd.DataFrame({"id": [2]})),
        ],
    )
    calls = 0

    def fail_second_write(_self, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("第二个 Sheet 写入失败")

    monkeypatch.setattr(pd.DataFrame, "to_sql", fail_second_write)
    service = ExcelImportService(gateway, upload_directory)
    request = ImportRequest(
        filePath=file_path.name,
        sheets=[
            SheetFields(sheetName="Sheet1", fields=[]),
            SheetFields(sheetName="Sheet2", fields=[]),
        ],
    )

    with pytest.raises(RuntimeError, match="第二个 Sheet 写入失败"):
        service.import_file(request)

    assert engine.transaction.exception_type is RuntimeError
    assert not file_path.exists()


def test_import_rejects_path_outside_upload_directory(tmp_path):
    upload_directory = tmp_path / "uploads"
    upload_directory.mkdir()
    outside_file = tmp_path / "outside.csv"
    outside_file.write_text("id\n1\n", encoding="utf-8")
    service = ExcelImportService(
        PostgreSQLExcelImportGateway(),
        upload_directory,
    )

    with pytest.raises(ExcelImportFileError, match="路径不合法"):
        service.import_file(_csv_request("../outside.csv"))

    assert outside_file.exists()
