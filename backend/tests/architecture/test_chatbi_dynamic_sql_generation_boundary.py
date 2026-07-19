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


def test_dynamic_sql_generation_service_only_depends_on_stable_ports():
    imports = _imports(
        _tree("apps/chatbi/services/dynamic_sql_generation_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_dynamic_sql_generation_delegates_to_chatbi():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "generate_with_sub_sql")

    assert "build_dynamic_sql_generation_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "self.llm.stream" not in source
    assert "dynamic_sys_question" not in source
    assert "dynamic_user_question" not in source
    assert "check_save_sql" not in source


def test_legacy_dynamic_sql_mapping_keeps_placeholder_replacement_contract():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(
        tree,
        "LLMService",
        "generate_assistant_dynamic_sql",
    )

    assert "DynamicSQLSubqueryMapping" in source
    assert "dynamic_subsql_prefix" in source
    assert "sqlbot_temp_sql_text" in source


def test_legacy_chat_question_no_longer_owns_dynamic_sql_templates():
    tree = _tree("apps/chat/models/chat_model.py")
    imports = _imports(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_dynamic.generator" not in imports
    assert "dynamic_sys_question" not in method_names
    assert "dynamic_user_question" not in method_names
