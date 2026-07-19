import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _imports(relative_path: str) -> set[str]:
    path = BACKEND_DIR / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_agent_sql_tools_only_use_chatbi_query_service():
    imports = _imports("apps/agent/tools/core.py")

    assert "apps.chatbi.services" in imports
    assert "apps.capabilities.sql.executor" not in imports
    assert "apps.capabilities.sql.permission" not in imports
    assert "apps.capabilities.sql.validator" not in imports


def test_graph_sql_adapter_does_not_maintain_second_execution_chain():
    path = BACKEND_DIR / "apps/workflow/capabilities/adapters/sql.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports("apps/workflow/capabilities/adapters/sql.py")

    assert "apps.chatbi.services" in imports
    assert "self._execute_tool" not in source
    assert "self._validate_tool" not in source
    assert "self._permission_adapter.apply" not in source


def test_chatbi_query_service_has_no_session_or_datasource_dependency():
    imports = _imports("apps/chatbi/services/query_service.py")

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_agent_and_graph_share_chatbi_semantic_query_service():
    agent_source = (
        BACKEND_DIR / "apps/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/sql.py"
    ).read_text(encoding="utf-8")

    assert "semantic_query_service.compile" in agent_source
    assert "compile_semantic_sql(" not in agent_source
    assert "_compile_semantic_query" in graph_source
    assert "self._compiler.compile" not in graph_source


def test_chatbi_semantic_query_service_has_no_session_or_repository_dependency():
    imports = _imports("apps/chatbi/services/semantic_query_service.py")

    assert "sqlmodel" not in imports
    assert not any(".repository" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)
