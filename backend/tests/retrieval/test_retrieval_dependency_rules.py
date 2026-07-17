"""统一检索域的依赖方向守卫。"""

import ast
from pathlib import Path

RETRIEVAL_DIR = Path(__file__).resolve().parents[2] / "apps" / "retrieval"
FORBIDDEN = (
    "apps.workflow_engine",
    "apps.workflow",
    "apps.agent",
    "apps.agentic_chat",
)
GENERIC_RETRIEVAL_MODULES = ("projection.py", "indexing.py", "models.py")


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


def test_generic_projection_and_indexing_modules_do_not_import_headless():
    for filename in GENERIC_RETRIEVAL_MODULES:
        path = RETRIEVAL_DIR / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("apps.headless"), f"{path} 反向依赖了 Headless"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("apps.headless"), f"{path} 反向依赖了 Headless"
