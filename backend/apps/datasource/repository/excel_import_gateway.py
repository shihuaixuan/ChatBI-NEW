from pathlib import Path
from typing import Protocol

from apps.datasource.models.dto import ExcelImportResult, SheetFields


class ExcelImportGateway(Protocol):
    """Excel/CSV 文件写入本地分析库的技术端口。"""

    def import_file(
        self,
        file_path: Path,
        sheets: list[SheetFields],
    ) -> ExcelImportResult: ...

    def import_all(self, file_path: Path) -> ExcelImportResult: ...
