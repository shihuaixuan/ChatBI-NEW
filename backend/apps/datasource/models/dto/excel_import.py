from pydantic import BaseModel, Field


class ImportedExcelSheet(BaseModel):
    sheetName: str
    tableName: str
    tableComment: str = ""
    rows: int


class ExcelImportResult(BaseModel):
    filename: str
    sheets: list[ImportedExcelSheet] = Field(default_factory=list)
