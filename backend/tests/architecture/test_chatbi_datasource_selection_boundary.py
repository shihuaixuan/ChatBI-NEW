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


def test_datasource_selection_service_only_depends_on_stable_ports():
    imports = _imports(
        _tree("apps/chatbi/services/datasource_selection_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_legacy_datasource_selection_delegates_model_and_binding_to_chatbi():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "select_datasource")

    assert "build_datasource_selection_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "service.bind_selection" in source
    assert "self.llm.stream" not in source
    assert "extract_nested_json" not in source
    assert "save_select_datasource_answer" not in source
    assert "_chat.datasource" not in source
    assert "_session.get(Chat" not in source


def test_legacy_datasource_selection_sse_contract_is_unchanged():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(tree, "LLMService", "run_task")

    assert "datasource-result" in source
    assert "datasource_name" in source
    assert "engine_type" in source


def test_legacy_chat_question_no_longer_owns_datasource_selection_template():
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

    assert "apps.template.select_datasource.generator" not in imports
    assert "datasource_sys_question" not in method_names
    assert "datasource_user_question" not in method_names
