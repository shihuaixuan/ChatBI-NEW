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


def _class_method_source(tree: ast.Module, class_name: str, name: str) -> str:
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.unparse(method)


def test_generation_history_projection_only_depends_on_chatbi_dto():
    imports = _imports(
        _tree("apps/chatbi/services/generation_history_projection_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_legacy_init_messages_delegates_history_projection_to_chatbi():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "init_messages")

    assert "GenerationHistoryProjectionService" in source
    assert "GenerationHistoryProjectionData" in source
    assert "GenerationHistoryLog" in source
    assert "get_last_conversation_rounds" not in source
    assert "sqlbot_system" not in source


def test_legacy_history_round_helper_is_removed():
    tree = _tree("apps/chat/task/llm.py")
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    assert "get_last_conversation_rounds" not in function_names
