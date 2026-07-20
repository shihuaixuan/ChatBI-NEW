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


def test_recommended_question_service_only_depends_on_stable_ports():
    imports = _imports(
        _tree("apps/chatbi/services/recommended_question_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_recommendation_task_only_keeps_schema_log_and_sse_projection():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(
        tree,
        "LLMService",
        "generate_recommend_questions_task",
    )

    assert "build_recommended_question_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "guess_sys_question" not in source
    assert "guess_user_question" not in source
    assert "get_old_questions" not in source
    assert "save_recommend_question_answer" not in source
    assert "self.llm.stream" not in source


def test_sql_model_adapter_uses_shared_model_stream_parser():
    adapter_tree = _tree("apps/chatbi/adapters/sql_generation.py")
    adapter_imports = _imports(adapter_tree)
    legacy_tree = _tree("apps/chat/task/llm.py")

    assert "apps.ai_model.streaming" in adapter_imports
    assert "apps.ai_model.streaming" not in _imports(legacy_tree)
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "process_stream"
        for node in adapter_tree.body
    )


def test_legacy_chat_question_no_longer_owns_recommendation_templates():
    tree = _tree("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_guess_question.generator" not in imports
    assert "guess_sys_question" not in method_names
    assert "guess_user_question" not in method_names
