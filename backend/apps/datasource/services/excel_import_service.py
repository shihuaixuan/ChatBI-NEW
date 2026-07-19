from pathlib import Path

from apps.datasource.models.dto import ExcelImportResult, ImportRequest
from apps.datasource.repository.excel_import_gateway import ExcelImportGateway


class ExcelImportFileError(ValueError):
    """Excel 临时文件不存在或路径不合法。"""


class ExcelImportService:
    """Excel 临时文件校验、导入和清理的应用流程。"""

    def __init__(
        self,
        gateway: ExcelImportGateway,
        upload_directory: Path,
    ) -> None:
        self._gateway = gateway
        self._upload_directory = upload_directory.resolve()

    def import_file(self, request: ImportRequest) -> ExcelImportResult:
        file_path = self._resolve_file(request.filePath)

        try:
            return self._gateway.import_file(file_path, request.sheets)
        finally:
            # 文件只服务于一次导入，成功或失败都必须清理。
            file_path.unlink(missing_ok=True)

    def import_all_file(self, filename: str) -> ExcelImportResult:
        file_path = self._resolve_file(filename)
        try:
            return self._gateway.import_all(file_path)
        finally:
            file_path.unlink(missing_ok=True)

    def _resolve_file(self, filename: str) -> Path:
        file_path = (self._upload_directory / filename).resolve()
        if file_path.parent != self._upload_directory:
            raise ExcelImportFileError("Excel 临时文件路径不合法")
        if not file_path.is_file():
            raise ExcelImportFileError("Excel 临时文件不存在")
        return file_path
