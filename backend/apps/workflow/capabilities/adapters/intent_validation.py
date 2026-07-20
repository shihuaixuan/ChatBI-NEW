"""Graph 旧意图校验路径的兼容导出，业务规则统一由 ChatBI 维护。"""

from apps.chatbi.services.understanding.graph_contracts import (
    QuestionIntentValidationService,
)

IntentPostProcessor = QuestionIntentValidationService
IntentValidationAdapter = QuestionIntentValidationService

__all__ = [
    "IntentPostProcessor",
    "IntentValidationAdapter",
    "QuestionIntentValidationService",
]
