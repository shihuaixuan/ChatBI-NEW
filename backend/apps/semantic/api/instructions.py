"""数据集 instructions 管理接口。"""

from fastapi import APIRouter

from apps.semantic.api.error_mapping import map_semantic_errors_to_http
from apps.semantic.composition import build_semantic_instruction_service
from apps.semantic.models.dto import InstructionModule, InstructionPayload
from apps.semantic.models.orm import SemanticDatasetInstruction
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Semantic"], prefix="/semantic")


@router.get("/datasets/{dataset_id}/instructions")
async def list_instructions(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
    module: InstructionModule | None = None,
    enabled_only: bool = False,
) -> list[SemanticDatasetInstruction]:
    with map_semantic_errors_to_http():
        return build_semantic_instruction_service(session).list(
            current_user.oid,
            dataset_id,
            module=module,
            enabled_only=enabled_only,
        )


@router.post("/datasets/{dataset_id}/instructions")
async def create_instruction(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
    payload: InstructionPayload,
) -> SemanticDatasetInstruction:
    with map_semantic_errors_to_http():
        return build_semantic_instruction_service(session).create(
            current_user.oid,
            dataset_id,
            payload,
        )


@router.put("/instructions/{instruction_id}")
async def update_instruction(
    session: SessionDep,
    current_user: CurrentUser,
    instruction_id: int,
    payload: InstructionPayload,
) -> SemanticDatasetInstruction:
    with map_semantic_errors_to_http():
        return build_semantic_instruction_service(session).update(
            current_user.oid,
            instruction_id,
            payload,
        )


@router.delete("/instructions/{instruction_id}")
async def delete_instruction(
    session: SessionDep,
    current_user: CurrentUser,
    instruction_id: int,
) -> dict[str, int | bool]:
    with map_semantic_errors_to_http():
        return build_semantic_instruction_service(session).delete(
            current_user.oid,
            instruction_id,
        )
