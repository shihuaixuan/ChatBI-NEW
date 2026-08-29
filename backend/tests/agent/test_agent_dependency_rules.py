"""ChatBI Agent 编排依赖规则守卫：禁止 import 图运行时与 v1。"""

import ast
from pathlib import Path

AGENT_DIR = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "chatbi"
    / "orchestration"
    / "agent"
)
FORBIDDEN = (
    "apps.agentic_chat",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_agent_never_imports_graph_or_v1():
    for path in AGENT_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for module in _imported_modules(path):
            for forbidden in FORBIDDEN:
                assert not module.startswith(forbidden), f"{path} import 了禁止的包 {module}"
