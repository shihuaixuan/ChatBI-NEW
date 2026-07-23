from apps.chatbi.models import (
    ChatLogHistory,
    ChatLogHistoryItem,
    ChatRecordResult,
)


def test_chat_record_result_keeps_optional_execution_type():
    assert ChatRecordResult().execution_type is None


def test_chat_log_history_steps_are_not_shared_between_instances():
    first = ChatLogHistory()
    second = ChatLogHistory()

    first.steps.append(ChatLogHistoryItem(operate="GENERATE_SQL"))

    assert len(first.steps) == 1
    assert second.steps == []
