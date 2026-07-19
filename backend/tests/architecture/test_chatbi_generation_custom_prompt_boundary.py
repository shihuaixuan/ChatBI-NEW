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


def test_generation_custom_prompt_service_only_depends_on_chatbi_dto():
    imports = _imports(
        _tree("apps/chatbi/services/generation_custom_prompt_service.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("sqlbot_xpack") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)


def test_xpack_custom_prompt_dependency_is_owned_by_infrastructure_adapter():
    imports = _imports(_tree("infrastructure/generation_custom_prompt.py"))

    assert "sqlbot_xpack.custom_prompt.curd.custom_prompt" in imports
    assert "sqlbot_xpack.custom_prompt.models.custom_prompt_model" in imports
    assert "sqlbot_xpack.license.license_manage" in imports


def test_legacy_llm_uses_chatbi_custom_prompt_contract():
    tree = _tree("apps/chat/task/llm.py")
    imports = _imports(tree)
    source = _class_method_source(tree, "LLMService", "filter_custom_prompts")

    assert not any(
        module.startswith("sqlbot_xpack.custom_prompt") for module in imports
    )
    assert "sqlbot_xpack.license.license_manage" not in imports
    assert "build_generation_custom_prompt_service" in source
    assert "GenerationCustomPromptQuery" in source
    assert "find_custom_prompts" not in source
    assert "SQLBotLicenseUtil" not in source
