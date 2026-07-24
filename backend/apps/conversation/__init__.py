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
from apps.conversation.formatting import (
    format_chart_fields,
    format_json_data,
    format_json_list_data,
    format_record,
)
from apps.conversation.models import (
    ChatFinishStep,
    ChatInfo,
    ChatLogHandle,
    ChatLogHistory,
    ChatLogHistoryItem,
    ChatRecordAuxiliaryProjection,
    ChatRecordAuxiliaryType,
    ChatRecordCreateData,
    ChatRecordExecutionType,
    ChatRecordLiveQuery,
    ChatRecordResult,
    ChatRecordResultLimits,
    ChatRecordResultProjection,
    ChatRecordStatus,
    ConversationBinding,
    ConversationCreateData,
    ConversationSnapshot,
    ConversationSummary,
    CreateChat,
    OperationEnum,
    RenameChat,
)
from apps.conversation.resource_scope import ConversationResourceScope
from apps.conversation.services.chat_log import ChatLogService
from apps.conversation.services.chat_record import (
    ChatRecordService,
    normalize_chat_record_status,
)
from apps.conversation.services.conversation import ConversationService
from apps.conversation.services.history_query import HistoryQueryService

__all__ = [
    "ChatFinishStep",
    "ChatInfo",
    "ChatLogHandle",
    "ChatLogHistory",
    "ChatLogHistoryItem",
    "ChatLogService",
    "ChatRecordAuxiliaryProjection",
    "ChatRecordAuxiliaryType",
    "ChatRecordCreateData",
    "ChatRecordError",
    "ChatRecordExecutionType",
    "ChatRecordLiveQuery",
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
    "ConversationSnapshot",
    "ConversationSummary",
    "ConversationError",
    "ConversationNotFoundError",
    "ConversationOwnershipError",
    "ConversationService",
    "ConversationServiceConfigurationError",
    "ConversationResourceScope",
    "CreateChat",
    "HistoryQueryService",
    "OperationEnum",
    "RenameChat",
    "format_chart_fields",
    "format_json_data",
    "format_json_list_data",
    "format_record",
    "normalize_chat_record_status",
]
