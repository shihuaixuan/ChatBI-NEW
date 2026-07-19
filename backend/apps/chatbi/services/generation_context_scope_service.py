from apps.chatbi.models.dto.generation_context import (
    GenerationContextScope,
    GenerationContextScopeData,
)

ADVANCED_ASSISTANT_TYPE = 1
PAGE_EMBEDDED_ASSISTANT_TYPE = 4


class GenerationContextScopeService:
    """统一提示词和 SQL 示例使用的工作空间与资源范围。"""

    def project(self, data: GenerationContextScopeData) -> GenerationContextScope:
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


__all__ = ["GenerationContextScopeService"]
