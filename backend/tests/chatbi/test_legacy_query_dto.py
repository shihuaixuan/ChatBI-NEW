from apps.chatbi.models import AiModelQuestion, ChatQuestion


def test_chat_question_keeps_legacy_defaults():
    question = ChatQuestion(chat_id=7)

    assert question.question is None
    assert question.lang == "简体中文"
    assert question.filter == []
    assert question.sqlbot_name == "Numora"
    assert question.datasource_id is None


def test_legacy_query_filter_is_not_shared_between_instances():
    first = ChatQuestion(chat_id=1)
    second = ChatQuestion(chat_id=2)

    assert isinstance(first.filter, list)
    first.filter.append("paid")

    assert first.filter == ["paid"]
    assert second.filter == []


def test_legacy_query_filter_still_accepts_string_input():
    question = AiModelQuestion(filter="paid = true")

    assert question.filter == "paid = true"
