from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationSchemaTableCandidate:
    """物理表相关性排序使用的稳定输入。"""

    table_id: int
    embedding: str | None


@dataclass(frozen=True, slots=True)
class GenerationSchemaContext:
    """SQL 生成使用的物理 Schema 与样例数据。"""

    schema: str
    sample_data: str = ""


__all__ = ["GenerationSchemaContext", "GenerationSchemaTableCandidate"]
