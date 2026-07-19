from apps.mcp.schemas import ChatStart, McpDs, McpQuestion


def test_mcp_question_keeps_request_defaults():
    request = McpQuestion(
        question="查询销售额",
        chat_id=7,
        token="token",
    )

    assert request.stream is True
    assert request.lang == "zh-CN"
    assert request.datasource_id is None
    assert request.oid is None
    assert request.return_img is True


def test_mcp_question_keeps_string_and_integer_datasource_compatibility():
    string_request = McpQuestion(
        question="查询销售额",
        chat_id=7,
        token="token",
        datasource_id="12",
    )
    integer_request = McpQuestion(
        question="查询销售额",
        chat_id=7,
        token="token",
        datasource_id=12,
    )

    assert string_request.datasource_id == "12"
    assert integer_request.datasource_id == 12


def test_mcp_schema_keeps_required_fields_and_descriptions():
    start_schema = ChatStart.model_json_schema()
    datasource_schema = McpDs.model_json_schema()
    question_schema = McpQuestion.model_json_schema()

    assert start_schema["required"] == ["username", "password"]
    assert start_schema["properties"]["username"]["description"] == "用户名"
    assert datasource_schema["required"] == ["token"]
    assert datasource_schema["properties"]["oid"]["default"] is None
    assert question_schema["required"] == ["question", "chat_id", "token"]
    assert question_schema["properties"]["stream"]["default"] is True
    assert question_schema["properties"]["return_img"]["default"] is True
