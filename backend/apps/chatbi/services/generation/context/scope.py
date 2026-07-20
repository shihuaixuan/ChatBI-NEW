"""生成流程的工作空间与资源范围规则（纯函数）。"""

from apps.chatbi.models.dto.generation_context import (
    GenerationContextScope,
    GenerationContextScopeData,
)

ADVANCED_ASSISTANT_TYPE = 1
DYNAMIC_DATASOURCE_ASSISTANT_TYPES = frozenset({1, 3})
PAGE_EMBEDDED_ASSISTANT_TYPE = 4


def resolve_generation_scope(data: GenerationContextScopeData) -> GenerationContextScope:
    """统一提示词和 SQL 示例使用的工作空间与资源范围。"""

    assistant = data.assistant
    if assistant is None:
        return GenerationContextScope(
            workspace_id=data.default_workspace_id,
            datasource_id=data.datasource_id,
        )

    workspace_id = (
        data.current_user_workspace_id
        if assistant.assistant_type == PAGE_EMBEDDED_ASSISTANT_TYPE
        else assistant.workspace_id
    )
    if assistant.assistant_type == ADVANCED_ASSISTANT_TYPE:
        return GenerationContextScope(
            workspace_id=workspace_id,
            datasource_id=None,
            sql_example_assistant_id=assistant.assistant_id,
            use_assistant_sql_examples=True,
        )
    return GenerationContextScope(
        workspace_id=workspace_id,
        datasource_id=data.datasource_id,
    )


__all__ = [
    "ADVANCED_ASSISTANT_TYPE",
    "DYNAMIC_DATASOURCE_ASSISTANT_TYPES",
    "PAGE_EMBEDDED_ASSISTANT_TYPE",
    "resolve_generation_scope",
]
