from typing import Protocol

from apps.agentic_chat.schemas import ToolResult


class AgenticTool(Protocol):
    name: str

    def run(self, payload: dict) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, AgenticTool] = {}

    def register(self, tool: AgenticTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgenticTool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def run(self, name: str, payload: dict) -> ToolResult:
        return self.get(name).run(payload)

    def names(self) -> list[str]:
        return sorted(self._tools)
