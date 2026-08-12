from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from apps.conversation import ChatRecordExecutionType


class ChatBIResultArtifactRef(BaseModel):
    """ChatBI 对外暴露的完整结果轻量引用。"""

    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size: int = Field(ge=0)
    digest: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResultArtifactWriteData(BaseModel):
    """一次执行结果 Artifact 的稳定写入请求。"""

    execution_id: str = Field(min_length=1, max_length=64)
    execution_type: ChatRecordExecutionType
    chat_id: int | None = Field(default=None, gt=0)
    record_id: int | None = Field(default=None, gt=0)
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_conversation_ownership(self) -> ResultArtifactWriteData:
        """会话型执行必须同时提供 chat_id 与 record_id。"""

        if (self.chat_id is None) != (self.record_id is None):
            raise ValueError("RESULT_ARTIFACT_OWNERSHIP_INCOMPLETE")
        return self


class ResultArtifactReadInput(BaseModel):
    """按执行归属读取一个 JSON Artifact。"""

    artifact_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1, max_length=64)
    execution_type: ChatRecordExecutionType
    chat_id: int = Field(gt=0)
    record_id: int = Field(gt=0)
    kind: str = Field(min_length=1, max_length=64)
    expected_metadata: dict[str, Any] = Field(default_factory=dict)


class ResultArtifactSnapshot(BaseModel):
    """校验完整性后读取到的 Artifact 快照。"""

    artifact_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size: int = Field(ge=0)
    digest: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]


__all__ = [
    "ChatBIResultArtifactRef",
    "ResultArtifactReadInput",
    "ResultArtifactSnapshot",
    "ResultArtifactWriteData",
]
