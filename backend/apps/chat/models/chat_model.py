"""sqlbot_xpack 尚未迁移的旧 Chat 模型导入路径兼容桩（台账 A7/B2）。"""

from apps.chatbi.models import (
    AiModelQuestion as AiModelQuestion,
)
from apps.chatbi.models import (
    Chat as Chat,
)
from apps.chatbi.models import (
    ChatFinishStep as ChatFinishStep,
)
from apps.chatbi.models import (
    ChatInfo as ChatInfo,
)
from apps.chatbi.models import (
    ChatLog as ChatLog,
)
from apps.chatbi.models import (
    ChatLogHistory as ChatLogHistory,
)
from apps.chatbi.models import (
    ChatLogHistoryItem as ChatLogHistoryItem,
)
from apps.chatbi.models import (
    ChatQuestion as ChatQuestion,
)
from apps.chatbi.models import (
    ChatRecord as ChatRecord,
)
from apps.chatbi.models import (
    ChatRecordResult as ChatRecordResult,
)
from apps.chatbi.models import (
    CreateChat as CreateChat,
)
from apps.chatbi.models import (
    OperationEnum as OperationEnum,
)
from apps.chatbi.models import (
    QuickCommand as QuickCommand,
)
from apps.chatbi.models import (
    RenameChat as RenameChat,
)
from apps.chatbi.models import (
    TypeEnum as TypeEnum,
)
from common.utils.data_format_schema import AxisObj as AxisObj

__all__ = [
    "AiModelQuestion",
    "AxisObj",
    "Chat",
    "ChatFinishStep",
    "ChatInfo",
    "ChatLog",
    "ChatLogHistory",
    "ChatLogHistoryItem",
    "ChatQuestion",
    "ChatRecord",
    "ChatRecordResult",
    "CreateChat",
    "OperationEnum",
    "QuickCommand",
    "RenameChat",
    "TypeEnum",
]
