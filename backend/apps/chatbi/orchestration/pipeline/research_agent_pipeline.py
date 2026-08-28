"""Research Agent 主路径的正式管道名称。

阶段 7 已将执行引擎切换为 ``ResearchAgentRuntime``；旧模块保留为导入兼容，
这里仅复用同一个管道实现，不创建第二套研究流程。
"""

from apps.chatbi.orchestration.pipeline.plan_and_solve_pipeline import (
    PlanAndSolvePipeline as ResearchAgentPipeline,
)
from apps.chatbi.orchestration.pipeline.plan_and_solve_pipeline import (
    PlanAndSolvePipelineDependencies as ResearchAgentPipelineDependencies,
)

__all__ = ["ResearchAgentPipeline", "ResearchAgentPipelineDependencies"]
