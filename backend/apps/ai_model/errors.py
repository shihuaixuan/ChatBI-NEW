"""AI Model 领域错误。"""


class AIModelError(Exception):
    """AI Model 领域错误基类。"""


class AIModelNotFoundError(AIModelError):
    def __init__(self, model_id: int) -> None:
        super().__init__(f"AI_MODEL_NOT_FOUND:{model_id}")


class DefaultAIModelNotConfiguredError(AIModelError):
    def __init__(self) -> None:
        super().__init__("AI_MODEL_DEFAULT_NOT_CONFIGURED")


class AIModelConfigInvalidError(AIModelError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"AI_MODEL_CONFIG_INVALID:{detail}")
