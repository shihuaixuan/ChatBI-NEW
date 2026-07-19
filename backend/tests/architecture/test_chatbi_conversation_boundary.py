import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_conversation_service_depends_on_ports_not_session_or_legacy_chat():
    path = BACKEND_DIR / "apps/chatbi/services/conversation_service.py"
    imports = _imports(path)

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource.") for module in imports)
    assert not any(module.startswith("apps.semantic.") for module in imports)
    assert not any(module.startswith("apps.knowledge.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine.") for module in imports)


def test_legacy_conversation_mutations_only_forward_to_service():
    path = BACKEND_DIR / "apps/chat/curd/chat.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        node.name: ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in (
        "list_chats",
        "rename_chat_with_user",
        "delete_chat_with_user",
        "create_chat",
    ):
        source = functions[name]
        assert "build_conversation_service" in source
        assert ".commit(" not in source
        assert ".flush(" not in source

    assert "rename_chat" not in functions
    assert "delete_chat" not in functions


def test_agent_conversation_owner_check_uses_chatbi_service():
    path = BACKEND_DIR / "apps/agent/crud.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_chat_for_user"
    )
    source = ast.unparse(function)

    assert "build_conversation_reader_service" in source
    assert ".get_owned(" in source
    assert "session.get(" not in source
