"""Conversation 领域错误。"""


class ConversationError(ValueError):
    """会话业务错误基类。"""


class ConversationBindingError(ConversationError):
    """会话数据集绑定不合法。"""


class ConversationNotFoundError(ConversationError):
    """会话不存在。"""


class ConversationOwnershipError(ConversationError):
    """当前用户不拥有会话。"""


class ConversationServiceConfigurationError(RuntimeError):
    """Conversation Service 配置不完整。"""


class ChatRecordError(ValueError):
    """问数记录业务错误基类。"""


class ChatRecordNotFoundError(ChatRecordError):
    """问数记录不存在。"""


class ChatRecordOwnershipError(ChatRecordError):
    """问数记录不属于指定会话。"""


class ChatRecordTransitionError(ChatRecordError):
    """问数记录状态转换不合法。"""


class ChatRecordResultTooLargeError(ChatRecordError):
    """问数结果超过会话快照限制。"""


__all__ = [
    "ChatRecordError",
    "ChatRecordNotFoundError",
    "ChatRecordOwnershipError",
    "ChatRecordResultTooLargeError",
    "ChatRecordTransitionError",
    "ConversationBindingError",
    "ConversationError",
    "ConversationNotFoundError",
    "ConversationOwnershipError",
    "ConversationServiceConfigurationError",
]
