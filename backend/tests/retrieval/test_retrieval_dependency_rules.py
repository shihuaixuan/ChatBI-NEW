"""统一检索域的依赖方向守卫。"""

import ast
from pathlib import Path

RETRIEVAL_DIR = Path(__file__).resolve().parents[2] / "apps" / "retrieval"
FORBIDDEN = (
    "apps.workflow_engine",
    "apps.chatbi_workflow",
    "apps.chatbi_agent",
    "apps.agentic_chat",
)


def test_retrieval_domain_does_not_import_orchestration_packages():
    for path in RETRIEVAL_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(FORBIDDEN), f"{path} import 了编排包 {alias.name}"
            if module:
                assert not module.startswith(FORBIDDEN), f"{path} import 了编排包 {module}"
