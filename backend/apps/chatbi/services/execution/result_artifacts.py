from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel

from apps.chatbi.models import ChatBIResultArtifactRef, ResultArtifactWriteData


class ResultArtifactError(RuntimeError):
    """结果 Artifact 处理错误基类。"""


class ResultArtifactWriteError(ResultArtifactError):
    """完整结果正文或元数据写入失败。"""


class ResultArtifactGateway(Protocol):
    """ChatBI 依赖的通用 Artifact 存储与清理端口。"""

    def put_json(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Any: ...

    def schedule_cleanup(
        self,
        *,
        metadata: dict[str, str | int],
        execution_ids: list[str] | None = None,
    ) -> int: ...

    def process_pending_cleanup(self) -> int: ...


class ResultArtifactService:
    """统一管理 Agent 与 Graph 完整结果的写入和会话级清理。"""

    _RESERVED_METADATA_KEYS = frozenset(
        {"execution_id", "execution_type", "chat_id", "record_id"}
    )

    def __init__(self, gateway: ResultArtifactGateway) -> None:
        self._gateway = gateway

    def save(self, data: ResultArtifactWriteData) -> ChatBIResultArtifactRef:
        metadata = {
            key: value
            for key, value in data.metadata.items()
            if key not in self._RESERVED_METADATA_KEYS
        }
        metadata.update(
            {
                "execution_id": data.execution_id,
                "execution_type": data.execution_type.value,
            }
        )
        if data.chat_id is not None:
            metadata["chat_id"] = data.chat_id
            metadata["record_id"] = data.record_id

        try:
            stored = self._gateway.put_json(
                run_id=data.execution_id,
                kind=data.kind,
                payload=data.payload,
                metadata=metadata,
            )
            return ChatBIResultArtifactRef.model_validate(
                _artifact_mapping(stored)
            )
        except ResultArtifactWriteError:
            raise
        except Exception as exc:
            # 存储异常必须转换为稳定业务错误，调用方不能误判为已存档。
            raise ResultArtifactWriteError(
                "RESULT_ARTIFACT_WRITE_FAILED"
            ) from exc

    def schedule_chat_cleanup(
        self,
        chat_id: int,
        *,
        legacy_execution_ids: list[str] | None = None,
    ) -> int:
        """在会话删除事务内登记正文清理任务并删除 Artifact 元数据。"""

        if chat_id <= 0:
            raise ValueError("RESULT_ARTIFACT_CHAT_ID_INVALID")
        return self._gateway.schedule_cleanup(
            metadata={"chat_id": chat_id},
            execution_ids=legacy_execution_ids,
        )

    def process_pending_cleanup(self) -> int:
        """仅在会话删除事务提交成功后处理正文清理。"""

        return self._gateway.process_pending_cleanup()


def _artifact_mapping(stored: Any) -> Mapping[str, Any]:
    if isinstance(stored, BaseModel):
        return stored.model_dump(mode="json")
    if isinstance(stored, Mapping):
        return stored
    raise ResultArtifactWriteError("RESULT_ARTIFACT_REFERENCE_INVALID")


__all__ = [
    "ResultArtifactError",
    "ResultArtifactGateway",
    "ResultArtifactService",
    "ResultArtifactWriteError",
]
