from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO


class TermWorkbookRow(SemanticBaseDTO):
    """术语工作簿中的稳定行契约。"""

    row_number: int
    domain_id: str = ""
    name: str = ""
    aliases: str = ""
    description: str = ""
    dataset_ids: str = ""
    enabled: str = "Y"
    error: str = ""


class TermWorkbookImportResult(SemanticBaseDTO):
    """术语工作簿导入结果。"""

    success_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    original_count: int = 0
    failed_rows: list[TermWorkbookRow] = Field(default_factory=list)
    error_workbook: bytes | None = Field(default=None, exclude=True)
