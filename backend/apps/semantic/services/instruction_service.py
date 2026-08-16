"""数据集 instructions 资产服务。"""

from datetime import datetime

from apps.semantic.errors import SemanticNotFoundError, SemanticValidationError
from apps.semantic.models.dto import InstructionPayload
from apps.semantic.models.orm import SemanticDatasetInstruction
from apps.semantic.repository.sqlmodel.instruction_repository import (
    SqlModelInstructionRepository,
)


class SemanticInstructionService:
    """校验模块范围并维护版本化指令。"""

    def __init__(self, repository: SqlModelInstructionRepository):
        self._repository = repository

    def list(
        self,
        oid: int,
        dataset_id: int,
        *,
        module: str | None = None,
        enabled_only: bool = False,
    ) -> list[SemanticDatasetInstruction]:
        return self._repository.list_for_dataset(
            oid,
            dataset_id,
            module=module,
            enabled_only=enabled_only,
        )

    def create(
        self,
        oid: int,
        dataset_id: int,
        payload: InstructionPayload,
    ) -> SemanticDatasetInstruction:
        existing = self._repository.list_for_dataset(
            oid,
            dataset_id,
            module=payload.module,
        )
        if any(item.version == payload.version for item in existing):
            raise SemanticValidationError("SEMANTIC_INSTRUCTION_VERSION_CONFLICT")
        return self._repository.create(
            SemanticDatasetInstruction(
                oid=oid,
                dataset_id=dataset_id,
                **payload.model_dump(),
            )
        )

    def update(
        self,
        oid: int,
        instruction_id: int,
        payload: InstructionPayload,
    ) -> SemanticDatasetInstruction:
        instruction = self._repository.get(oid, instruction_id)
        if instruction is None:
            raise SemanticNotFoundError("SEMANTIC_INSTRUCTION_NOT_FOUND")
        existing = self._repository.list_for_dataset(
            oid,
            instruction.dataset_id,
            module=payload.module,
        )
        if any(item.id != instruction_id and item.version == payload.version for item in existing):
            raise SemanticValidationError("SEMANTIC_INSTRUCTION_VERSION_CONFLICT")
        for key, value in payload.model_dump().items():
            setattr(instruction, key, value)
        instruction.updated_at = datetime.now()
        return self._repository.update(instruction)

    def delete(self, oid: int, instruction_id: int) -> dict[str, int | bool]:
        instruction = self._repository.get(oid, instruction_id)
        if instruction is None:
            raise SemanticNotFoundError("SEMANTIC_INSTRUCTION_NOT_FOUND")
        self._repository.delete(instruction)
        return {"id": instruction_id, "deleted": True}


__all__ = ["SemanticInstructionService"]
