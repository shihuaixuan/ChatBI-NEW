import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _tree(relative_path: str) -> ast.Module:
    path = BACKEND_DIR / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_common_data_format_does_not_depend_on_chat_models():
    imports = _imports(_tree("common/utils/data_format.py"))
    schema_imports = _imports(_tree("common/utils/data_format_schema.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.chat") for module in schema_imports)


def test_legacy_chat_model_only_contains_compatibility_exports():
    tree = _tree("apps/chat/models/chat_model.py")

    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    assert _imports(tree) == {
        "apps.chatbi.models",
        "common.utils.data_format_schema",
    }


def test_chat_runtime_uses_public_axis_schema():
    api_imports = _imports(_tree("apps/chat/api/chat.py"))
    task_imports = _imports(_tree("apps/chat/task/llm.py"))

    assert "common.utils.data_format_schema" in api_imports
    assert "common.utils.data_format_schema" in task_imports
