from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from apps.chatbi.errors import (
    ResultArtifactError,
    ResultArtifactReadError,
    ResultArtifactWriteError,
)
from apps.chatbi.models import (
    ChatBIResultArtifactRef,
    ResultArtifactReadInput,
    ResultArtifactSnapshot,
    ResultArtifactWriteData,
)
from apps.chatbi.services.execution.ports import ResultArtifactGateway


class ResultArtifactService:
    """统一管理 Agent、Graph 与命名结果集的完整正文和会话级清理。"""

    LEGACY_SQL_RESULT_KIND = "sql_result"
    NAMED_RESULT_SET_KIND = "analysis_result_set"

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
            return ChatBIResultArtifactRef.model_validate(_artifact_mapping(stored))
        except ResultArtifactWriteError:
            raise
        except Exception as exc:
            # 存储异常必须转换为稳定业务错误，调用方不能误判为已存档。
            raise ResultArtifactWriteError("RESULT_ARTIFACT_WRITE_FAILED") from exc

    def save_named_result_set(
        self,
        data: ResultArtifactWriteData,
        *,
        result_set_id: str,
    ) -> ChatBIResultArtifactRef:
        """写入命名结果集，并集中校验结果集 ID 元数据。"""

        if data.kind != self.NAMED_RESULT_SET_KIND:
            raise ValueError("RESULT_SET_ARTIFACT_KIND_REQUIRED")
        if data.metadata.get("result_set_id") != result_set_id:
            raise ValueError("RESULT_SET_ARTIFACT_ID_MISMATCH")
        return self.save(data)

    def read(self, data: ResultArtifactReadInput) -> ResultArtifactSnapshot:
        """读取 Artifact，并在返回正文前验证执行与会话归属。"""

        try:
            snapshot = ResultArtifactSnapshot.model_validate(
                _artifact_mapping(self._gateway.get_json(data.artifact_id))
            )
        except ResultArtifactReadError:
            raise
        except Exception as exc:
            raise ResultArtifactReadError(ResultArtifactReadError.READ_FAILED) from exc

        expected_metadata = {
            "execution_id": data.execution_id,
            "execution_type": data.execution_type.value,
            "chat_id": data.chat_id,
            "record_id": data.record_id,
            **data.expected_metadata,
        }
        ownership_matches = (
            snapshot.artifact_id == data.artifact_id
            and snapshot.run_id == data.execution_id
            and snapshot.kind == data.kind
            and all(
                snapshot.metadata.get(key) == value
                for key, value in expected_metadata.items()
            )
        )
        if not ownership_matches:
            raise ResultArtifactReadError(ResultArtifactReadError.OWNERSHIP_MISMATCH)
        return snapshot

    def find_by_idempotency_key(
        self,
        *,
        execution_id: str,
        kind: str,
        idempotency_key: str,
    ) -> ResultArtifactSnapshot | None:
        """从持久化存储恢复同一执行节点已经写入的 Artifact。"""

        stored = self._gateway.find_json(
            run_id=execution_id,
            kind=kind,
            idempotency_key=idempotency_key,
        )
        if stored is None:
            return None
        return ResultArtifactSnapshot.model_validate(_artifact_mapping(stored))

    def read_named_result_set(
        self,
        data: ResultArtifactReadInput,
        *,
        result_set_id: str,
    ) -> ResultArtifactSnapshot:
        """读取命名结果集，并要求读取契约携带同一结果集 ID。"""

        if data.kind != self.NAMED_RESULT_SET_KIND:
            raise ValueError("RESULT_SET_ARTIFACT_KIND_REQUIRED")
        expected_metadata = {
            **data.expected_metadata,
            "result_set_id": result_set_id,
        }
        return self.read(
            data.model_copy(update={"expected_metadata": expected_metadata})
        )

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
    "ResultArtifactReadError",
    "ResultArtifactService",
    "ResultArtifactWriteError",
]
