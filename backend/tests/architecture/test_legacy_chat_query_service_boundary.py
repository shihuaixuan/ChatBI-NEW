import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _tree(relative_path: str) -> ast.Module:
    path = BACKEND_DIR / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


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


def test_legacy_chat_sql_execution_delegates_to_query_service():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "execute_sql")

    assert "build_legacy_chat_query_service" in source
    assert ".execute_sql" in source
    assert "exec_sql" not in source
    assert "workspace_id=self.current_user.oid" in source
    assert "user_id=self.current_user.id" in source


def test_legacy_chat_run_task_keeps_table_scope_for_internal_datasource():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "run_task")

    assert "allowed_tables=None if use_dynamic_ds else tables" in source
    assert "execute-success" in source
    assert "sql-data" in source


def test_connection_driver_dependency_stays_in_chatbi_adapter():
    legacy_tree = _tree("apps/chat/task/llm.py")
    adapter_tree = _tree("apps/chatbi/adapters/query_execution.py")
    legacy_source = ast.unparse(legacy_tree)
    adapter_source = ast.unparse(adapter_tree)

    assert "exec_sql(" not in legacy_source
    assert "exec_sql(" in adapter_source
