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


def test_mcp_request_schemas_do_not_depend_on_chat_or_fastapi():
    imports = _imports(_tree("apps/mcp/schemas.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert "fastapi" not in imports
    assert "sqlmodel" not in imports


def test_mcp_routes_use_owned_request_schemas():
    imports = _imports(_tree("apps/mcp/mcp.py"))

    assert "apps.mcp.schemas" in imports
    assert "apps.chat.models.chat_model" not in imports


def test_legacy_chat_model_has_no_mcp_or_fastapi_contract():
    tree = _tree("apps/chat/models/chat_model.py")
    imports = _imports(tree)
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "fastapi" not in imports
    assert "McpDs" not in class_names
    assert "ChatStart" not in class_names
    assert "McpQuestion" not in class_names
