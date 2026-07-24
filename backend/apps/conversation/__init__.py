"""Conversation 领域公共入口。

其他模块只能通过本包导出的 DTO、Service 和组合入口访问会话数据，
不得导入本包的 ORM 或 SQLModel 仓储实现。
"""

from apps.conversation.errors import (
    ChatRecordError,
    ChatRecordNotFoundError,
    ChatRecordOwnershipError,
    ChatRecordResultTooLargeError,
    ChatRecordTransitionError,
    ConversationBindingError,
    ConversationError,
    ConversationNotFoundError,
    ConversationOwnershipError,
    ConversationServiceConfigurationError,
)
from apps.conversation.models import (
    ChatInfo,
    ChatLogHistory,
    ChatLogHistoryItem,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordResult,
    ChatRecordResultLimits,
    ChatRecordResultProjection,
    ChatRecordStatus,
    ConversationBinding,
    ConversationCreateData,
    ConversationSummary,
    CreateChat,
    RenameChat,
)
from apps.conversation.resource_scope import ConversationResourceScope
from apps.conversation.services.chat_record import (
    ChatRecordService,
    normalize_chat_record_status,
)
from apps.conversation.services.conversation import ConversationService

__all__ = [
    "ChatInfo",
    "ChatLogHistory",
    "ChatLogHistoryItem",
    "ChatRecordAuxiliaryProjection",
    "ChatRecordAuxiliaryType",
    "ChatRecordCreateData",
    "ChatRecordError",
    "ChatRecordExecutionType",
    "ChatRecordNotFoundError",
    "ChatRecordOwnershipError",
    "ChatRecordResult",
    "ChatRecordResultLimits",
    "ChatRecordResultProjection",
    "ChatRecordResultTooLargeError",
    "ChatRecordService",
    "ChatRecordStatus",
    "ChatRecordTransitionError",
    "ConversationBinding",
    "ConversationBindingError",
    "ConversationCreateData",
    "ConversationSummary",
    "ConversationError",
    "ConversationNotFoundError",
    "ConversationOwnershipError",
    "ConversationService",
    "ConversationServiceConfigurationError",
    "ConversationResourceScope",
    "CreateChat",
    "RenameChat",
    "normalize_chat_record_status",
]
