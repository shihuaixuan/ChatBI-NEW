"""Assistant 领域错误。"""


class AssistantError(Exception):
    """Assistant 领域错误基类。"""


class AssistantNotFoundError(AssistantError, ValueError):
    def __init__(self, assistant_id: int | str) -> None:
        super().__init__(f"AssistantModel with id {assistant_id} not found")


class AssistantWorkspaceMismatchError(AssistantError, PermissionError):
    def __init__(self, assistant_id: int, workspace_id: int) -> None:
        super().__init__(f"ASSISTANT_WORKSPACE_MISMATCH:{assistant_id}:{workspace_id}")


class AssistantConfigurationError(AssistantError, ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"ASSISTANT_CONFIGURATION_INVALID:{reason}")


class AssistantDatasourceScopeError(AssistantError, ValueError):
    def __init__(self, datasource_ids: list[int]) -> None:
        joined_ids = ",".join(str(value) for value in sorted(datasource_ids))
        super().__init__(f"ASSISTANT_DATASOURCE_OUT_OF_SCOPE:{joined_ids}")


class AssistantCustomModelError(AssistantError, ValueError):
    def __init__(self, model_id: str | None) -> None:
        super().__init__(f"ASSISTANT_CUSTOM_MODEL_INVALID:{model_id or ''}")


class AssistantExternalDatasourceError(AssistantError, RuntimeError):
    """外部数据源接口或返回结构不可用。"""


class AssistantTokenError(AssistantError, ValueError):
    """助手令牌缺少必要信息或与应用不匹配。"""
