"""数据集 instructions 的 API 与运行时契约。"""

from typing import Literal

from pydantic import Field

from apps.semantic.models.dto.base import SemanticBaseDTO

InstructionModule = Literal["sql_generation", "question_categorization"]


class InstructionPayload(SemanticBaseDTO):
    module: InstructionModule
    content: str = Field(min_length=1, max_length=12000)
    version: int = Field(default=1, ge=1)
    enabled: bool = True


class InstructionRecord(InstructionPayload):
    id: int
    oid: int
    dataset_id: int


__all__ = ["InstructionModule", "InstructionPayload", "InstructionRecord"]
