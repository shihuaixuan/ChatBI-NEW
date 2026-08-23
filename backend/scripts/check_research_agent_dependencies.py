"""检查阶段 1新 Research Agent 契约没有依赖旧 ResearchAction。

该守卫使用 AST 扫描明确列出的新模块，不依赖整文件哈希。阶段 2以后新增的
Research Agent 模块必须显式加入 ``NEW_MODULES``，避免守卫范围随目录变化而失真。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NEW_MODULES = (
    BACKEND_ROOT / "apps" / "chatbi" / "models" / "dto" / "research_agent.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "execution" / "analysis_execution.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "semantic_query_builder.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "semantic_runtime.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "tool_context.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "state_snapshot.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "run_lifecycle.py",
    BACKEND_ROOT / "apps" / "chatbi" / "services" / "research" / "agent_context.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "agent" / "tools" / "research.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "agent" / "reasoning_profile.py",
    BACKEND_ROOT / "apps" / "chatbi" / "orchestration" / "pipeline" / "research_agent.py",
)
FORBIDDEN_MODULE_PREFIXES = (
    "apps.chatbi.services.research.actions",
    "apps.chatbi.services.research.action_batches",
)
FORBIDDEN_NAMES = {
    "ResearchActionType",
    "ResearchPolicyDecision",
    "ResearchAction",
}


def _module_name(node: ast.ImportFrom) -> str:
    if node.level:
        return "." * node.level + (node.module or "")
    return node.module or ""


def check_dependencies() -> list[str]:
    errors: list[str] = []
    for path in NEW_MODULES:
        if not path.is_file():
            errors.append(f"阶段 1新模块不存在: {path}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _module_name(node)
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_MODULE_PREFIXES
                ):
                    errors.append(f"{path}: 禁止导入旧模块 {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_NAMES:
                        errors.append(f"{path}: 禁止导入旧符号 {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == prefix or alias.name.startswith(prefix + ".")
                        for prefix in FORBIDDEN_MODULE_PREFIXES
                    ):
                        errors.append(f"{path}: 禁止导入旧模块 {alias.name}")
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
    print("Research Agent 阶段 1依赖边界检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
