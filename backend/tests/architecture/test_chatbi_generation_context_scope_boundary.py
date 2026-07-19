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


def test_generation_context_scope_service_only_depends_on_chatbi_dto():
    imports = _imports(
        _tree("apps/chatbi/services/generation_context_scope_service.py")
    )

    assert imports == {"apps.chatbi.models.dto.generation_context"}


def test_legacy_prompt_and_example_filters_use_shared_scope():
    tree = _tree("apps/chat/task/llm.py")

    for method_name in ("filter_custom_prompts", "filter_training_template"):
        source = _class_method_source(tree, "LLMService", method_name)
        assert "resolve_generation_context_scope" in source
        assert "current_assistant.type" not in source
        assert "calculate_oid" not in source
        assert "calculate_ds_id" not in source


def test_legacy_scope_method_only_adapts_context_to_chatbi():
    source = _class_method_source(
        _tree("apps/chat/task/llm.py"),
        "LLMService",
        "resolve_generation_context_scope",
    )

    assert "GenerationContextScopeService" in source
    assert "GenerationContextScopeData" in source
    assert "GenerationAssistantContext" in source
    assert "assistant_type ==" not in source
    assert "assistant_type !=" not in source
