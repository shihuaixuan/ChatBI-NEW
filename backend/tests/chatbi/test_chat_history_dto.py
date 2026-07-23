from apps.chat.models import chat_model
from apps.chatbi.models import (
    ChatLogHistory,
    ChatLogHistoryItem,
    ChatRecordResult,
)


def test_xpack_chat_history_models_are_chatbi_compatibility_exports():
    assert chat_model.ChatRecordResult is ChatRecordResult
    assert chat_model.ChatLogHistoryItem is ChatLogHistoryItem
    assert chat_model.ChatLogHistory is ChatLogHistory


def test_chat_record_result_keeps_optional_execution_type():
    assert ChatRecordResult().execution_type is None


def test_chat_log_history_steps_are_not_shared_between_instances():
    first = ChatLogHistory()
    second = ChatLogHistory()

    first.steps.append(ChatLogHistoryItem(operate="GENERATE_SQL"))

    assert len(first.steps) == 1
    assert second.steps == []
