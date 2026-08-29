"""检查 Research ReAct 主路径的依赖边界和旧协议残留。

阶段 8～10 已物理删除旧架构（Action/Policy/物化器、shadow 双跑、
rollout 采样、旧契约 DTO 和 Planner/Solve 研究入口）。本守卫做两个方向的断言：

1. 反向：全仓 ``apps/`` 与 ``scripts/`` 下任何模块都不得导入已删除模块，
   也不得引用旧 Research DTO、Observation、快照键和 submit_research_plan。
2. Research 生产路径不得保留 plan_execution_state；Fast/通用 planning
   的合法使用不在该扫描范围内。
3. 正向：当前 Research 主路径模块必须仍然存在，且不引用旧符号。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 阶段 8 已物理删除的模块前缀：任何 import 都构成回归。
DELETED_MODULE_PREFIXES = (
    "apps.chatbi.models.dto.research",
    "apps.chatbi.services.research.actions",
    "apps.chatbi.services.research.action_batches",
    "apps.chatbi.services.research.policy",
    "apps.chatbi.services.research.policy_rules",
    "apps.chatbi.services.research.hypotheses",
    "apps.chatbi.services.research.evidence",
    "apps.chatbi.services.research.report",
    "apps.chatbi.services.research.shadow",
    "apps.chatbi.services.research.rollout",
    "apps.chatbi.services.research.requirements",
    "apps.chatbi.orchestration.pipeline.research",
    "apps.chatbi.adapters.prompts.research_policy",
    # 2026-08-28 冗余清理追加：以下模块已随本轮删除落地，同样禁止回归。
    "apps.chatbi.adapters.chart_generation",
    "apps.chatbi.adapters.datasource_selection",
    "apps.chatbi.adapters.dynamic_sql_generation",
    "apps.chatbi.adapters.permission_sql_generation",
    "apps.chatbi.adapters.prompts.limited_multistep",
    "apps.chatbi.adapters.query_result_projection",
    "apps.chatbi.adapters.sql_generation",
    "apps.chatbi.models.dto.legacy_query",
    "apps.chatbi.orchestration.agent.prompts",
    "apps.chatbi.orchestration.agent.tool_execution",
    "apps.chatbi.orchestration.pipeline.events",
    "apps.chatbi.orchestration.pipeline.fast",
    "apps.chatbi.orchestration.pipeline.mode_router_legacy",
    "apps.chatbi.orchestration.pipeline.plan_mode",
    "apps.chatbi.orchestration.pipeline.research_agent",
    "apps.chatbi.orchestration.pipeline.stages",
    "apps.chatbi.services.planning.limited_multistep",
    # 2026-08-28 Graph 引擎退役：引擎包与 Graph 编排目录已物理删除，禁止回归。
    "apps.chatbi.orchestration.graph",
    "sqlbot_platform",
    "platform.workflow_engine",
)

# 旧架构独有符号：现行 ReAct 的 Action 和 State 契约不属于删除范围。
FORBIDDEN_NAMES = {
    "ResearchPolicyDecision",
    "ResearchBreakdownAction",
    "ResearchFilterFromResultAction",
    "EvidenceSnapshot",
    "ResearchRequirement",
    "ResearchPlanNode",
    "ResearchCompletion",
    "ResearchAgentReport",
    "ToolObservation",
    "ToolObservationStatus",
    "ResearchBudgetUsage",
    "ResearchRunStatus",
    "ResearchCompletionReason",
    "SemanticAssessmentStatus",
    "StructuralCoverage",
    "StructuralCoverageGap",
    "validate_plan_required_operations",
}

# 旧协议的字符串键也属于边界的一部分。state_snapshot.py 需要保留
# research_run_snapshot 的拒绝分支，以便旧持久化数据显式失败，因此单独放行。
FORBIDDEN_RESEARCH_KEYS = {
    "research_run_snapshot",
    "failed_observations",
    "hypothesis_assessments",
    "final_report",
    "report_draft",
    "submit_research_plan",
    "plan_execution_state",
}
ALLOWED_LEGACY_REJECTION_KEYS = {
    "state_snapshot.py": {"research_run_snapshot"},
}

# 阶段 1 起的新契约模块：必须存在并保持无旧符号。
NEW_MODULES = (
    BACKEND_ROOT / "apps" / "chatbi" / "models" / "dto" / "research_agent.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "execution" / "analysis_execution.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "routing_freeze.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "semantic_query_builder.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "semantic_runtime.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "tool_context.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "state_snapshot.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "run_lifecycle.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "agent_context.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "agent" / "reasoning_profile.py",
    BACKEND_ROOT
    / "apps"
    / "chatbi"
    / "orchestration"
    / "pipeline"
    / "research_agent_runtime.py",
    BACKEND_ROOT
    / "apps"
    / "chatbi"
    / "orchestration"
    / "pipeline"
    / "research_agent_pipeline.py",
)

SCAN_ROOTS = (
    BACKEND_ROOT / "apps",
    BACKEND_ROOT / "scripts",
    BACKEND_ROOT / "main.py",
)


def _is_research_production_path(path: Path) -> bool:
    """判断文件是否属于 Research 主路径，排除 Fast 的计划执行状态。"""

    normalized = path.as_posix()
    return (
        "/apps/chatbi/services/research/" in normalized
        or path.name in {
            "research_agent.py",
            "research_agent_pipeline.py",
            "research_agent_runtime.py",
            "reasoning_profile.py",
        }
    )


def _module_name(node: ast.ImportFrom) -> str:
    if node.level:
        return "." * node.level + (node.module or "")
    return node.module or ""


def _is_deleted_module(module: str) -> bool:
    # 精确前缀匹配：models.dto.research 不得误伤 research_agent。
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in DELETED_MODULE_PREFIXES
    )


def check_dependencies() -> list[str]:
    errors: list[str] = []

    # 反向：全仓扫描，禁止任何对已删除模块的导入。
    for root in SCAN_ROOTS:
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*.py"))
        else:
            continue
        for path in candidates:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                errors.append(f"{path}: 语法错误 {exc}")
                continue
            research_path = _is_research_production_path(path)
            eval_script = path.name == "run_research_agent_eval.py"
            allowed_keys = ALLOWED_LEGACY_REJECTION_KEYS.get(path.name, set())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = _module_name(node)
                    if _is_deleted_module(module):
                        errors.append(f"{path}: 引用已删除模块 {module}")
                    for alias in node.names:
                        if alias.name in FORBIDDEN_NAMES:
                            errors.append(f"{path}: 引用旧 Research 符号 {alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_deleted_module(alias.name):
                            errors.append(f"{path}: 引用已删除模块 {alias.name}")
                        if alias.asname in FORBIDDEN_NAMES:
                            errors.append(f"{path}: 引用旧 Research 符号 {alias.asname}")
                elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                    errors.append(f"{path}: 引用旧 Research 符号 {node.id}")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value == "plan_execution_state" and research_path:
                        errors.append(f"{path}: Research 路径引用 plan_execution_state")
                    elif (
                        node.value in FORBIDDEN_RESEARCH_KEYS - {"plan_execution_state"}
                        and (research_path or eval_script)
                        and node.value not in allowed_keys
                    ):
                        errors.append(f"{path}: Research 路径引用旧协议键 {node.value}")

    # 正向：新契约模块必须存在，且不引用旧架构符号。
    for path in NEW_MODULES:
        if not path.is_file():
            errors.append(f"新契约模块不存在: {path}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in FORBIDDEN_NAMES:
                        errors.append(f"{path}: 禁止导入旧符号 {alias.name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                errors.append(f"{path}: 禁止引用旧符号 {node.id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="执行依赖边界检查")
    args = parser.parse_args()
    if not args.check:
        parser.error("必须显式传入 --check")
    errors = check_dependencies()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Research 阶段 10 边界检查通过：旧协议无残留引用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
