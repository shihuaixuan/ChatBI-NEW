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


def test_legacy_llm_adapter_only_depends_on_serialization_library():
    imports = _imports(_tree("apps/chat/task/legacy_adapter.py"))

    assert imports == {"collections.abc", "typing", "orjson"}


def test_legacy_chat_streams_use_shared_sse_encoder():
    llm_source = (BACKEND_DIR / "apps/chat/task/llm.py").read_text(encoding="utf-8")
    api_source = (BACKEND_DIR / "apps/chat/api/chat.py").read_text(encoding="utf-8")

    assert "from apps.chat.task.legacy_adapter import" in llm_source
    assert "from apps.chat.task.legacy_adapter import encode_sse_event" in api_source
    assert "'data:' + orjson.dumps" not in llm_source
    assert "'data:' + orjson.dumps" not in api_source


def test_legacy_llm_prompt_logs_use_shared_adapter():
    source = (BACKEND_DIR / "apps/chat/task/llm.py").read_text(encoding="utf-8")

    assert "build_role_prompt_log(" in source
    assert "build_context_prompt_log(" in source
    assert "'sqlbot_system': message.role == 'system'" not in source
    assert "'sqlbot_system': message.system_context" not in source
