import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _imports(relative_path: str) -> set[str]:
    tree = ast.parse((BACKEND_DIR / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_answer_generation_service_has_no_executor_or_framework_dependency():
    imports = _imports("apps/chatbi/services/answer_generation_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_answer_adapter_uses_chatbi_service_and_shared_model_boundary():
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")

    assert "AnswerGenerationService" in graph_source
    assert "AnswerGenerationData" in graph_source
    assert "build_question_model_service" in graph_source
    assert "DefaultAnswerModelClient" not in graph_source
    assert "LLMFactory" not in graph_source
    assert "get_default_config" not in graph_source
    assert "SystemMessage" not in graph_source
    assert "HumanMessage" not in graph_source
    assert "langchain" not in graph_source
    assert "def _extract_json_object" not in graph_source
    assert "def _answer_dump" not in graph_source
    assert "def _fallback_with_warning" not in graph_source


def test_answer_prompt_and_output_contract_are_owned_by_chatbi():
    service_source = (
        BACKEND_DIR / "apps/chatbi/services/answer_generation_service.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")
    schema_source = (BACKEND_DIR / "apps/workflow/schemas/v1.py").read_text(
        encoding="utf-8"
    )

    assert "def build_answer_generation_prompt" in service_source
    assert "def build_answer_generation_prompt" not in graph_source
    assert "class AnswerOutput(AnswerGenerationResult)" in schema_source
    assert "class AnswerGenerationResult" not in schema_source


def test_graph_keeps_answer_context_projection_and_final_composition():
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")

    assert "def build_answer_projection" in graph_source
    assert "def compose" in graph_source
    assert "FinalReplyOutput" in graph_source
    assert "ChatBIRunContext" in graph_source


def test_answer_projection_service_has_no_executor_or_framework_dependency():
    imports = _imports("apps/chatbi/services/answer_projection_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_graph_answer_projection_only_reads_context_and_calls_chatbi_service():
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")

    assert "AnswerProjectionService" in graph_source
    assert "AnswerProjectionData" in graph_source
    assert "projection_service.project" not in graph_source
    assert "service.project" in graph_source
    assert "def _project_plan" not in graph_source
    assert "def _project_execution_result" not in graph_source
    assert "def _project_validation" not in graph_source
    assert "def _project_multi_query_analysis" not in graph_source
    assert "def _results_by_role" not in graph_source
    assert "def _share_analysis" not in graph_source
    assert "def _comparison_analysis" not in graph_source
    assert "def _numeric_value" not in graph_source
    assert "def _project_error" not in graph_source
