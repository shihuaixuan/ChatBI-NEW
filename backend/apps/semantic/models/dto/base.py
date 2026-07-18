from pydantic import BaseModel, ConfigDict


class SemanticBaseDTO(BaseModel):
    """语义模块 DTO 基类。"""

    model_config = ConfigDict(from_attributes=True)
