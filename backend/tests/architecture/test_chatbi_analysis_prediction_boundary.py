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


def test_analysis_prediction_service_only_depends_on_stable_ports():
    imports = _imports(
        _tree("apps/chatbi/services/analysis_prediction_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_analysis_and_prediction_delegate_generation_to_chatbi():
    tree = _tree("apps/chat/task/llm.py")

    for method_name in ("generate_analysis", "generate_predict"):
        source = _class_method_source(tree, "LLMService", method_name)
        assert "build_analysis_prediction_service" in source
        assert "service.prepare" in source
        assert "service.generate" in source
        assert "start_log" in source
        assert "end_log" in source
        assert "self.llm.stream" not in source
        assert "save_analysis_answer" not in source
        assert "save_predict_answer" not in source


def test_legacy_analysis_and_prediction_sse_contract_is_unchanged():
    tree = _tree("apps/chat/task/llm.py")
    source = _class_method_source(
        tree,
        "LLMService",
        "run_analysis_or_predict_task",
    )

    for event_type in (
        "analysis-result",
        "analysis_finish",
        "predict-result",
        "predict-success",
        "predict-failed",
        "predict_finish",
    ):
        assert event_type in source


def test_legacy_chat_question_no_longer_owns_analysis_prediction_templates():
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

    assert "apps.template.generate_analysis.generator" not in imports
    assert "apps.template.generate_predict.generator" not in imports
    assert "analysis_sys_question" not in method_names
    assert "analysis_user_question" not in method_names
    assert "predict_sys_question" not in method_names
    assert "predict_user_question" not in method_names
