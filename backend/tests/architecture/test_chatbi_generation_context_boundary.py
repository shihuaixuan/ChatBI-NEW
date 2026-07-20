import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _imports(relative_path: str) -> set[str]:
    tree = ast.parse((BACKEND_DIR / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_generation_context_uses_other_domains_public_services_only():
    imports = _imports("apps/chatbi/services/generation_context_service.py")

    assert "apps.knowledge.services" in imports
    assert "apps.semantic.services" in imports
    assert not any(
        ".repository" in module
        or ".crud" in module
        or ".models.orm" in module
        or module.endswith(".composition")
        for module in imports
    )


def test_generation_runtime_settings_is_a_pure_projection():
    imports = _imports(
        "apps/chatbi/services/generation_runtime_settings_service.py"
    )

    assert not any(
        module.startswith(("apps.system", "sqlmodel", "sqlbot_xpack"))
        for module in imports
    )


def test_generation_schema_context_uses_public_domain_services_only():
    imports = _imports(
        "apps/chatbi/services/generation_schema_context_service.py"
    )

    assert "apps.datasource.services" in imports
    assert "apps.access_control.services" in imports
    assert not any(
        ".repository" in module
        or ".crud" in module
        or ".models.orm" in module
        or module.endswith(".composition")
        for module in imports
    )


def test_datasource_selection_candidates_use_public_domain_services_only():
    imports = _imports(
        "apps/chatbi/services/datasource_selection_candidate_service.py"
    )

    assert "apps.assistant.services" in imports
    assert "apps.datasource.services" in imports
    assert not any(
        ".repository" in module
        or ".crud" in module
        or ".models.orm" in module
        or module.endswith(".composition")
        for module in imports
    )


def test_legacy_dependencies_no_longer_read_local_schema_through_crud():
    imports = _imports("apps/chat/task/legacy_dependencies.py")

    assert "apps.datasource.crud.datasource" not in imports
    assert "apps.datasource.embedding.ds_embedding" not in imports
    assert "get_assistant_ds" not in (
        BACKEND_DIR / "apps/chat/task/legacy_dependencies.py"
    ).read_text(encoding="utf-8")
    assert "get_assistant_ds" not in (
        BACKEND_DIR / "apps/chat/task/llm.py"
    ).read_text(encoding="utf-8")
    assert not (
        BACKEND_DIR / "apps/datasource/embedding/ds_embedding.py"
    ).exists()


def test_legacy_llm_keeps_unmigrated_dependencies_in_legacy_module():
    imports = _imports("apps/chat/task/llm.py")

    assert "apps.chat.task.legacy_dependencies" in imports
    assert "apps.chatbi.composition" in imports
    assert "apps.system.composition" in imports
    assert "apps.knowledge.composition" not in imports
    assert "apps.semantic.composition" not in imports
    assert "apps.datasource.crud.datasource" not in imports
    assert "apps.datasource.models.datasource" not in imports
    assert "apps.system.crud.parameter_manage" not in imports
    assert "apps.ai_model.model_factory" not in imports
    assert "langchain.chat_models.base" not in imports
    assert "sqlbot_xpack.config.model" not in imports
    assert not (BACKEND_DIR / "apps/chat/services/term_context.py").exists()


def test_top_level_infrastructure_package_has_been_removed():
    assert not (BACKEND_DIR / "infrastructure").exists()
