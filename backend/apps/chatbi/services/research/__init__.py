"""Research 模式和 Research Agent 的语义查询运行服务。"""

from typing import TYPE_CHECKING, Any

from apps.chatbi.services.research.action_fingerprint import (
    build_research_action_fingerprint,
    research_action_fingerprint,
)
from apps.chatbi.services.research.ports import (
    PreparedResearchAction,
    ResearchTool,
    ResearchToolCostEstimate,
)
from apps.chatbi.services.research.runtime import (
    ResearchToolRegistry,
    ResearchToolRuntime,
)
from apps.chatbi.services.research.semantic_query_builder import SemanticQueryBuilder

if TYPE_CHECKING:
    from apps.chatbi.services.research.tools import (
        ComputeEvidenceResearchTool,
        QuerySemanticDataResearchTool,
        ReadEvidenceRowsResearchTool,
        build_research_data_tool_registry,
    )

__all__ = [
    "PreparedResearchAction",
    "ResearchTool",
    "ResearchToolCostEstimate",
    "ResearchToolRegistry",
    "ResearchToolRuntime",
    "ComputeEvidenceResearchTool",
    "QuerySemanticDataResearchTool",
    "ReadEvidenceRowsResearchTool",
    "SemanticQueryBuilder",
    "build_research_action_fingerprint",
    "build_research_data_tool_registry",
    "research_action_fingerprint",
]


def __getattr__(name: str) -> Any:
    """惰性导出阶段 4工具，避免服务包初始化时形成循环导入。"""

    if name in {
        "ComputeEvidenceResearchTool",
        "QuerySemanticDataResearchTool",
        "ReadEvidenceRowsResearchTool",
        "build_research_data_tool_registry",
    }:
        from apps.chatbi.services.research import tools

        return getattr(tools, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
