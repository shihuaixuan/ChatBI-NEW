"""能力层依赖规则守卫：禁止 import 运行时包。

规则见 apps/capabilities/__init__.py。semantic/retrieval.py 是垫片期
唯一例外（函数内延迟 import workflow），Step 4 下沉后该例外必须移除。

只检查真实 import 语句（AST），不误伤 docstring/注释中对包名的文字说明。
"""

import ast
from pathlib import Path

CAPABILITIES_DIR = Path(__file__).resolve().parents[2] / "apps" / "capabilities"
FORBIDDEN = ("apps.workflow_engine", "apps.agent", "apps.agentic_chat")
SHIM_EXCEPTION_FILE = CAPABILITIES_DIR / "semantic" / "retrieval.py"


def _py_files():
    return [path for path in CAPABILITIES_DIR.rglob("*.py") if "__pycache__" not in path.parts]


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_capabilities_never_import_runtime_packages():
    for path in _py_files():
        for module in _imported_modules(path):
            for forbidden in FORBIDDEN:
                assert not module.startswith(forbidden), f"{path} import 了禁止的运行时包 {module}"


def test_workflow_import_only_in_retrieval_shim():
    for path in _py_files():
        imports_workflow = any(module.startswith("apps.workflow") for module in _imported_modules(path))
        if imports_workflow:
            assert path == SHIM_EXCEPTION_FILE, f"workflow 只允许出现在检索垫片中，违规文件: {path}"
