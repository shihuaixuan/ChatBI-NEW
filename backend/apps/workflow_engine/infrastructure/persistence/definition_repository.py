from apps.workflow_engine.domain.definition import WorkflowDefinition


class DefinitionRepository:
    """数据库 Definition Repository 占位边界。

    Task 17 会补齐发布治理 API。当前先保留明确模块，避免调用方直接依赖表模型。
    """

    def serialize(self, definition: WorkflowDefinition) -> dict:
        return definition.model_dump(mode="json")
