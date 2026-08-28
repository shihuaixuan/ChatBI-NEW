"""Research Agent Runtime 的兼容导出入口。"""

from apps.chatbi.orchestration.pipeline.research_agent_runtime import (
    ResearchAgentRunOutcome,
    ResearchAgentRuntime,
)

# 旧 Harness 名称只作为导入兼容，实际类型已经是新的 ReAct Runtime。
ResearchAgentHarness = ResearchAgentRuntime
_RESULT_SETS_KEY = "result_sets"

__all__ = [
    "ResearchAgentHarness",
    "ResearchAgentRunOutcome",
    "ResearchAgentRuntime",
]
