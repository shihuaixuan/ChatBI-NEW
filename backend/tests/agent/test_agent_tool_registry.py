from langchain_core.messages import AIMessage
from pydantic import BaseModel

from apps.chatbi.orchestration.agent.messages import AgentMessage
from apps.chatbi.orchestration.agent.model_client import DefaultAgentModelClient
from apps.chatbi.orchestration.agent.tools.base import (
    AgentTool,
    AgentToolContext,
)
from apps.chatbi.orchestration.agent.tools.core import (
    FinishTool,
)
from apps.chatbi.orchestration.agent.tools.interaction import ClarifyTool
from apps.chatbi.orchestration.agent.tools.temporal import ParseTimeRangeTool
from apps.tool import ToolCall, ToolRegistry, ToolResult, ToolStatus
from apps.tool.tools.datasource import (
    ExecuteSqlTool,
    GetDatasetSchemaTool,
    ValidateSqlTool,
)
from apps.tool.tools.knowledge import GetSqlExamplesTool
from apps.tool.tools.semantic import (
    CompileSemanticSqlTool,
    SearchSemanticAssetsTool,
    SearchTerminologyTool,
)


class EchoArgs(BaseModel):
    text: str


class EchoResult(BaseModel):
    echo: str


class EchoTool(AgentTool):
    name = "echo"
    description = "echo"
    args_model = EchoArgs
    result_model = EchoResult

    def execute(self, ctx, args):
        return ToolResult.succeeded(args.text, EchoResult(echo=args.text))


def _ctx():
    return AgentToolContext(session=None, oid=1, user_id=1, datasource_id=1)


def test_rejects_unregistered_tool():
    registry = ToolRegistry()
    result = registry.execute(ToolCall("missing", {}, "missing-1"), _ctx())
    assert result.status == ToolStatus.REJECTED
    assert result.error_code == "tool_not_allowed"
    assert "不在白名单" in result.model_content


def test_rejects_invalid_args():
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.execute(ToolCall("echo", {"wrong": 1}, "echo-1"), _ctx())
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "invalid_tool_args"


def test_executes_registered_tool_and_exposes_specs():
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = registry.execute(ToolCall("echo", {"text": "hi"}, "echo-1"), _ctx())
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data == EchoResult(echo="hi")
    definitions = registry.definitions()
    assert definitions[0].name == "echo"
    assert "text" in definitions[0].input_schema["properties"]


def test_all_production_tools_have_input_and_output_schema():
    tools = [
        SearchSemanticAssetsTool(object(), object()),  # type: ignore[arg-type]
        CompileSemanticSqlTool(object(), object()),  # type: ignore[arg-type]
        FinishTool(),
        ClarifyTool(),
        GetDatasetSchemaTool(object()),  # type: ignore[arg-type]
        ValidateSqlTool(object()),  # type: ignore[arg-type]
        ExecuteSqlTool(object()),  # type: ignore[arg-type]
        SearchTerminologyTool(object()),  # type: ignore[arg-type]
        GetSqlExamplesTool(object()),  # type: ignore[arg-type]
        ParseTimeRangeTool(),
    ]
    assert len(tools) == 10
    assert len({tool.name for tool in tools}) == 10
    for tool in tools:
        definition = tool.definition()
        assert definition.input_schema["type"] == "object"
        assert definition.output_schema
        assert tool.execution.timeout_seconds is not None
        assert tool.execution.timeout_seconds > 0


def test_default_model_client_isolates_langchain_message_and_tool_conversion():
    class RecordingModel:
        def __init__(self):
            self.specs = []
            self.messages = []
            self.tool_choice = None

        def bind_tools(self, specs, **kwargs):
            self.specs = specs
            self.tool_choice = kwargs.get("tool_choice")
            return self

        def invoke(self, messages):
            self.messages = messages
            return AIMessage(
                content="调用两个工具",
                additional_kwargs={"reasoning_content": "先规划调用顺序"},
                tool_calls=[
                    {"name": "echo", "args": {"text": "a"}, "id": "1", "type": "tool_call"},
                    {"name": "echo", "args": {"text": "b"}, "id": "2", "type": "tool_call"},
                ],
                usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            )

    model = RecordingModel()
    client = DefaultAgentModelClient()
    client._llm = model  # type: ignore[assignment]  # 测试模型适配边界，不创建真实模型。

    decision = client.invoke(
        [AgentMessage.system("system"), AgentMessage.user("hello")],
        [EchoTool.definition()],
    )

    assert [message.type for message in model.messages] == ["system", "human"]
    assert model.specs[0]["function"]["parameters"] == EchoTool.definition().input_schema
    assert [call.call_id for call in decision.tool_calls] == ["1", "2"]
    assert decision.message.tool_calls == decision.tool_calls
    assert decision.message.reasoning_content == "先规划调用顺序"
    assert decision.usage["total_tokens"] == 5

    client.invoke([decision.message], [EchoTool.definition()])
    assert model.messages[0].additional_kwargs["reasoning_content"] == "先规划调用顺序"
    assert model.tool_choice is None
