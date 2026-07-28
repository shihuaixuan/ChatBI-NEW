"""Tool 模型协议适配器。"""

from apps.tool.adapters.openai import to_openai_tool_spec, to_openai_tool_specs

__all__ = ["to_openai_tool_spec", "to_openai_tool_specs"]
