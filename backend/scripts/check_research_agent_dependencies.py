"""检查阶段 8 删除边界：旧 ResearchAction 架构不得再被引用。

阶段 8（doc38 §12 / doc39 §7）已物理删除旧架构（Action/Policy/物化器/
shadow 双跑/rollout 采样/旧契约 DTO）。本守卫做两个方向的断言：

1. 反向：全仓 ``apps/`` 与 ``scripts/`` 下任何模块都不得导入已删除模块，
   也不得从新契约模块导入旧符号。
2. 正向：阶段 1 列出的新契约模块必须仍然存在，且不引用旧符号。
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
    "apps.chatbi.services.research.ports",
    "apps.chatbi.services.research.report",
    "apps.chatbi.services.research.shadow",
    "apps.chatbi.services.research.rollout",
    "apps.chatbi.services.research.requirements",
    "apps.chatbi.orchestration.pipeline.research",
    "apps.chatbi.orchestration.pipeline.research_agent",
    "apps.chatbi.orchestration.pipeline.research_agent_pipeline",
    "apps.chatbi.adapters.prompts.research_policy",
)

# 旧架构独有符号：即使模块被重建，这些名字也不得回流新契约模块。
FORBIDDEN_NAMES = {
    "ResearchActionType",
    "ResearchAction",
    "ResearchPolicyDecision",
    "ResearchBreakdownAction",
    "ResearchFilterFromResultAction",
    "EvidenceSnapshot",
    "ResearchRequirement",
    "ResearchState",
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
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "completion.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "hypothesis_evaluator.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "report_validator.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "report_draft.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "agent" / "tools" / "research.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "agent" / "reasoning_profile.py",
    BACKEND_ROOT
    / "apps"
    / "chatbi"
    / "orchestration"
    / "pipeline"
    / "plan_and_solve_runtime.py",
    BACKEND_ROOT
    / "apps"
    / "chatbi"
    / "orchestration"
    / "pipeline"
    / "plan_and_solve_pipeline.py",
)

SCAN_ROOTS = (
    BACKEND_ROOT / "apps",
    BACKEND_ROOT / "scripts",
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
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                errors.append(f"{path}: 语法错误 {exc}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = _module_name(node)
                    if _is_deleted_module(module):
                        errors.append(f"{path}: 引用已删除模块 {module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_deleted_module(alias.name):
                            errors.append(f"{path}: 引用已删除模块 {alias.name}")

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
    print("Research 阶段 8 删除边界检查通过：旧架构无残留引用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
