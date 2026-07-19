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


def test_query_result_projection_service_only_depends_on_stable_boundaries():
    imports = _imports(
        _tree("apps/chatbi/services/query_result_projection_service.py")
    )

    assert "sqlmodel" not in imports
    assert "common.utils.data_format" not in imports
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)
    assert not any(module.startswith("apps.chatbi.models.orm") for module in imports)


def test_legacy_chat_run_task_delegates_result_projection_and_owns_transaction():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "run_task")

    assert "build_query_result_projection_service" in source
    assert "QueryResultProjectionData" in source
    assert ".project" in source
    assert "_session.commit()" in source
    assert "_session.rollback()" in source
    assert "convert_large_numbers_in_object_array" not in source
    assert "normalize_qualified_sql_column_keys_in_object_array" not in source
    assert "save_sql_data" not in source


def test_legacy_chat_result_writer_is_removed():
    tree = _tree("apps/chat/task/llm.py")
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LLMService"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }
    source = ast.unparse(tree)

    assert "save_sql_data" not in method_names
    assert "save_sql_exec_data" not in source
    assert "prepare_for_orjson" not in source


def test_legacy_chat_result_flow_keeps_existing_output_contract():
    source = _class_method_source(
        _tree("apps/chat/task/llm.py"),
        "LLMService",
        "run_task",
    )

    assert "execute-success" in source
    assert "sql-data" in source
    assert "to_markdown" in source
    assert "generate_chart" in source
    assert "request_picture" in source
