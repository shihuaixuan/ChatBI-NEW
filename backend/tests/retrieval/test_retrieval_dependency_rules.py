"""统一检索域的依赖方向守卫。"""

import ast
from pathlib import Path

RETRIEVAL_DIR = Path(__file__).resolve().parents[2] / "apps" / "retrieval"
FORBIDDEN = (
    "sqlbot_platform.workflow_engine",
    "apps.workflow",
    "apps.agent",
    "apps.agentic_chat",
)
GENERIC_RETRIEVAL_MODULES = (
    "projection/contracts.py",
    "indexing/service.py",
    "models/orm/retrieval.py",
    "models/dto/retrieval.py",
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


def test_generic_projection_and_indexing_modules_do_not_import_headless():
    for filename in GENERIC_RETRIEVAL_MODULES:
        path = RETRIEVAL_DIR / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("apps.semantic"), f"{path} 反向依赖了 Semantic"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("apps.semantic"), f"{path} 反向依赖了 Semantic"


def test_retrieval_runtime_uses_structured_orm_and_dto_imports():
    legacy_modules = {"apps.retrieval.models", "apps.retrieval.schemas"}
    for path in RETRIEVAL_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in legacy_modules, f"{path} 仍通过兼容入口导入模型"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in legacy_modules, f"{path} 仍通过兼容入口导入模型"


def test_retrieval_compatibility_model_modules_are_removed():
    assert not (RETRIEVAL_DIR / "schemas.py").exists()
    assert not (RETRIEVAL_DIR / "models" / "__init__.py").exists()
