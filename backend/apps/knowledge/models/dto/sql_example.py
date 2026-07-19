from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SQLExampleInput(BaseModel):
    """兼容现有 SQL 示例维护和 Excel 导入的请求。"""

    id: int | None = None
    oid: int | None = None
    datasource: int | None = None
    datasource_name: str | None = None
    create_time: datetime | None = None
    question: str | None = None
    description: str | None = None
    example_type: str | None = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict[str, Any]] | None = Field(default_factory=list)
    dataset_id: int | None = None
    enabled: bool | None = True
    advanced_application: int | None = None
    advanced_application_name: str | None = None


class SQLExampleRecord(BaseModel):
    """Repository 与 Service 之间使用的 SQL 示例快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    oid: int
    datasource: int | None = None
    create_time: datetime | None = None
    question: str
    description: str
    example_type: str = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict[str, Any]] = Field(default_factory=list)
    dataset_id: int | None = None
    enabled: bool = True
    advanced_application: int | None = None


class SQLExampleResult(BaseModel):
    """兼容现有列表与导出字段的 SQL 示例响应。"""

    id: str | None = None
    oid: str | None = None
    datasource: int | None = None
    datasource_name: str | None = None
    create_time: datetime | None = None
    question: str | None = None
    description: str | None = None
    example_type: str = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict[str, Any]] = Field(default_factory=list)
    dataset_id: int | None = None
    enabled: bool = True
    advanced_application: str | None = None
    advanced_application_name: str | None = None


class SQLExamplePage(BaseModel):
    current_page: int
    page_size: int
    total_count: int
    total_pages: int
    data: list[SQLExampleResult]


class SQLExampleMatch(BaseModel):
    id: int
    question: str
    suggestion_answer: str

    def to_legacy_dict(self) -> dict[str, str]:
        return {
            "question": self.question,
            "suggestion-answer": self.suggestion_answer,
        }


class SQLExampleErrorDetail(BaseModel):
    message_key: str
    format_args: list[str] = Field(default_factory=list)


class SQLExampleImportFailure(BaseModel):
    data: SQLExampleInput
    errors: list[SQLExampleErrorDetail]


class SQLExampleImportResult(BaseModel):
    success_count: int
    failed_records: list[SQLExampleImportFailure]
    duplicate_count: int
    original_count: int
    deduplicated_count: int
