import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class SQLExampleVerificationStatus(str, Enum):
    """SQL 示例内容与引用关系的独立验证状态。"""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class SQLExampleLinkedAsset(BaseModel):
    """SQL 示例可引用的 Semantic 指标或维度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_type: str = Field(
        validation_alias=AliasChoices("asset_type", "assetType", "type")
    )
    asset_id: int = Field(
        gt=0,
        validation_alias=AliasChoices("asset_id", "assetId", "id"),
    )

    @field_validator("asset_type")
    @classmethod
    def validate_asset_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"METRIC", "DIMENSION"}:
            raise ValueError("SQL_EXAMPLE_LINKED_ASSET_TYPE_INVALID")
        return normalized


class SQLExampleDatasetScope(BaseModel):
    """Knowledge 从 Semantic 公开目录读取的数据集引用范围。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: int = Field(gt=0)
    datasource_ids: tuple[int, ...] = ()
    metric_ids: tuple[int, ...] = ()
    dimension_ids: tuple[int, ...] = ()


class SQLExampleInput(BaseModel):
    """兼容现有 SQL 示例维护和 Excel 导入的请求。"""

    id: int | None = None
    oid: int | None = None
    datasource: int | None = Field(default=None, gt=0)
    datasource_name: str | None = None
    create_time: datetime | None = None
    question: str | None = None
    description: str | None = None
    example_type: str | None = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[SQLExampleLinkedAsset] | None = Field(default_factory=list)
    dataset_id: int | None = Field(default=None, gt=0)
    enabled: bool | None = True
    advanced_application: int | None = Field(default=None, gt=0)
    advanced_application_name: str | None = None


class SQLExampleRecord(BaseModel):
    """Repository 与 Service 之间使用的 SQL 示例快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    oid: int
    datasource: int | None = Field(default=None, gt=0)
    create_time: datetime | None = None
    question: str
    description: str
    example_type: str = "QUESTION_EXAMPLE"
    sql: str | None = None
    linked_assets: list[dict[str, Any]] = Field(default_factory=list)
    dataset_id: int | None = Field(default=None, gt=0)
    enabled: bool = True
    advanced_application: int | None = Field(default=None, gt=0)
    verification_status: SQLExampleVerificationStatus = (
        SQLExampleVerificationStatus.UNVERIFIED
    )


class SQLExampleSnapshot(BaseModel):
    """Retrieval 只读的单条 SQL 示例公开快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(gt=0)
    question: str = Field(min_length=1)
    description: str = Field(min_length=1)
    example_type: str = Field(min_length=1)
    sql: str | None = None
    linked_assets: tuple[SQLExampleLinkedAsset, ...] = ()
    dataset_id: int | None = Field(default=None, gt=0)
    datasource_id: int | None = Field(default=None, gt=0)
    assistant_id: int | None = Field(default=None, gt=0)

    @classmethod
    def from_record(cls, record: SQLExampleRecord) -> "SQLExampleSnapshot":
        if record.id is None:
            raise ValueError("SQL_EXAMPLE_NOT_PERSISTED")
        assets_by_key = {
            (asset.asset_type, asset.asset_id): asset
            for asset in (
                SQLExampleLinkedAsset.model_validate(value)
                for value in record.linked_assets
            )
        }
        return cls(
            id=record.id,
            question=record.question,
            description=record.description,
            example_type=record.example_type,
            sql=record.sql,
            linked_assets=tuple(assets_by_key.values()),
            dataset_id=record.dataset_id,
            datasource_id=record.datasource,
            assistant_id=record.advanced_application,
        )


class SQLExampleSourceSnapshot(BaseModel):
    """一个工作空间内全部启用 SQL 示例的完整来源快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: int = Field(gt=0)
    source_version: str = Field(min_length=1, max_length=128)
    examples: tuple[SQLExampleSnapshot, ...] = ()

    @classmethod
    def from_records(
        cls,
        workspace_id: int,
        records: list[SQLExampleRecord],
    ) -> "SQLExampleSourceSnapshot":
        examples = tuple(
            sorted(
                (
                    SQLExampleSnapshot.from_record(record)
                    for record in records
                    if record.enabled
                    and record.verification_status
                    == SQLExampleVerificationStatus.VERIFIED
                ),
                key=lambda item: item.id,
            )
        )
        encoded = json.dumps(
            [item.model_dump(mode="json") for item in examples],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return cls(
            workspace_id=workspace_id,
            source_version=f"snapshot:{digest}",
            examples=examples,
        )


class SQLExampleIndexEnqueueResult(BaseModel):
    """Knowledge 接收的 Retrieval durable job 提交结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: int = Field(gt=0)
    generation: str = Field(min_length=1)
    job_ids: tuple[int, ...] = ()


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
    verification_status: SQLExampleVerificationStatus = (
        SQLExampleVerificationStatus.UNVERIFIED
    )
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
