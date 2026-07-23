from pydantic import BaseModel

from apps.chatbi.orchestration.agent.tools.base import (
    AgentTool,
    AgentToolContext,
    ToolOutput,
)
from apps.chatbi.orchestration.agent.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str


class EchoTool(AgentTool):
    name = "echo"
    description = "echo"
    args_model = EchoArgs

    def execute(self, ctx, args):
        return ToolOutput(success=True, summary=args.text, payload={"echo": args.text})


def _ctx():
    return AgentToolContext(session=None, oid=1, user_id=1, datasource_id=1)


def test_rejects_unregistered_tool():
    registry = ToolRegistry()
    output = registry.execute("missing", _ctx(), {})
    assert not output.success
    assert output.error_code == "tool_not_allowed"
    assert "不在白名单" in output.summary


def test_rejects_invalid_args():
    registry = ToolRegistry()
    registry.register(EchoTool())
    output = registry.execute("echo", _ctx(), {"wrong": 1})
    assert not output.success
    assert output.error_code == "invalid_tool_args"


def test_executes_registered_tool_and_exposes_specs():
    registry = ToolRegistry()
    registry.register(EchoTool())
    output = registry.execute("echo", _ctx(), {"text": "hi"})
    assert output.success
    assert output.payload == {"echo": "hi"}
    specs = registry.tool_specs()
    assert specs[0]["function"]["name"] == "echo"
    assert "text" in specs[0]["function"]["parameters"]["properties"]
