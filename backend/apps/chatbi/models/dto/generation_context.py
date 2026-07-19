from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationAssistantContext:
    """生成上下文所需的稳定助手信息。"""

    assistant_id: int | None
    workspace_id: int | None
    assistant_type: int


@dataclass(frozen=True, slots=True)
class GenerationContextScopeData:
    """提示词和 SQL 示例范围投影输入。"""

    default_workspace_id: int | None
    current_user_workspace_id: int | None
    datasource_id: int | None
    assistant: GenerationAssistantContext | None = None


@dataclass(frozen=True, slots=True)
class GenerationContextScope:
    """提示词与 SQL 示例查询使用的统一范围。"""

    workspace_id: int | None
    datasource_id: int | None
    sql_example_assistant_id: int | None = None
    use_assistant_sql_examples: bool = False


__all__ = [
    "GenerationAssistantContext",
    "GenerationContextScope",
    "GenerationContextScopeData",
]
