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


def test_agent_and_graph_share_question_understanding_validation_service():
    agent_imports = _imports("apps/chatbi/services/question_understanding_service.py")
    graph_imports = _imports("apps/workflow/capabilities/adapters/intent_validation.py")

    service_module = "apps.chatbi.services.question_understanding_validation_service"
    assert service_module in agent_imports
    assert service_module in graph_imports


def test_graph_intent_adapter_does_not_reimplement_dimension_time_rule():
    question_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")
    validation_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/intent_validation.py"
    ).read_text(encoding="utf-8")

    assert "def _is_time_expression" not in question_source
    assert "_dimension_time_value_violations" not in validation_source


def test_question_validation_service_has_no_executor_or_model_dependency():
    imports = _imports("apps/chatbi/services/question_understanding_validation_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert "sqlmodel" not in imports


def test_question_model_service_has_no_executor_or_model_framework_dependency():
    imports = _imports("apps/chatbi/services/question_model_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_agent_and_graph_share_question_model_service():
    agent_source = (
        BACKEND_DIR / "apps/chatbi/services/question_understanding_service.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    for source in (agent_source, graph_source):
        assert "QuestionModelService" in source
        assert "QuestionModelInvocationData" in source
        assert "LLMFactory" not in source
        assert "get_default_config" not in source
        assert "SystemMessage" not in source
        assert "HumanMessage" not in source

    assert "QuestionModelJSONMode.EXTRACT_OBJECT" in graph_source
    assert "QuestionModelJSONMode.EXTRACT_OBJECT" not in agent_source
    assert "build_question_model_service" not in agent_source
    assert "infrastructure.question_model" not in agent_source


def test_default_question_model_client_stays_in_infrastructure():
    adapter_source = (
        BACKEND_DIR / "infrastructure/question_model.py"
    ).read_text(encoding="utf-8")
    agent_source = (
        BACKEND_DIR / "apps/chatbi/services/question_understanding_service.py"
    ).read_text(encoding="utf-8")
    composition_source = (BACKEND_DIR / "apps/chatbi/composition.py").read_text(
        encoding="utf-8"
    )
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "class LangChainQuestionModelClient" in adapter_source
    assert "LLMFactory" in adapter_source
    assert "DefaultQuestionUnderstandingModelClient" not in agent_source
    assert "DefaultQuestionClassificationModelClient" not in graph_source
    assert "build_question_model_service" in composition_source


def test_question_understanding_dtos_are_owned_by_chatbi():
    agent_source = (
        BACKEND_DIR / "apps/capabilities/question_understanding.py"
    ).read_text(encoding="utf-8")
    graph_source = (BACKEND_DIR / "apps/workflow/schemas/v1.py").read_text(
        encoding="utf-8"
    )

    for class_name in (
        "QuestionRewriteOutput",
        "DimensionSlot",
        "TimeRange",
        "IntentRecognitionOutput",
        "DimensionRecognitionOutput",
        "IntentValidationOutput",
        "QuestionUnderstandingOutput",
        "QuestionUnderstandingOutcome",
    ):
        assert f"class {class_name}" not in agent_source

    assert "QuestionRewriteOutputBase" in graph_source
    assert "NaturalLanguageIntentOutputBase" in graph_source


def test_question_understanding_prompt_rules_are_owned_by_chatbi():
    prompt_import = "apps.chatbi.services.question_understanding_prompt"
    agent_imports = _imports("apps/chatbi/services/question_understanding_service.py")
    graph_imports = _imports("apps/workflow/capabilities/adapters/question.py")
    prompt_imports = _imports("apps/chatbi/services/question_understanding_prompt.py")

    assert prompt_import in agent_imports
    assert prompt_import in graph_imports
    assert not any(module.startswith("apps.agent") for module in prompt_imports)
    assert not any(module.startswith("apps.workflow") for module in prompt_imports)
    assert not any(module.startswith("apps.ai_model") for module in prompt_imports)
    assert not any(module.startswith("langchain") for module in prompt_imports)
    assert "sqlmodel" not in prompt_imports


def test_question_understanding_service_has_no_executor_or_infrastructure_dependency():
    imports = _imports("apps/chatbi/services/question_understanding_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_old_question_understanding_path_only_reexports_chatbi_objects():
    compatibility_path = "apps/capabilities/question_understanding.py"
    compatibility_source = (BACKEND_DIR / compatibility_path).read_text(encoding="utf-8")
    compatibility_imports = _imports(compatibility_path)
    agent_loop_source = (BACKEND_DIR / "apps/agent/loop.py").read_text(encoding="utf-8")

    assert compatibility_imports == {
        "apps.chatbi.models.dto.question_understanding",
        "apps.chatbi.services.question_understanding_service",
    }
    assert "class QuestionUnderstandingService" not in compatibility_source
    assert "def apply_question_understanding_clarification" not in compatibility_source
    assert "apps.capabilities.question_understanding" not in agent_loop_source


def test_graph_intent_projection_rules_are_owned_by_chatbi():
    service_path = "apps/chatbi/services/question_intent_projection_service.py"
    service_imports = _imports(service_path)
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert not any(module.startswith("apps.agent") for module in service_imports)
    assert not any(module.startswith("apps.workflow") for module in service_imports)
    assert not any(module.startswith("apps.ai_model") for module in service_imports)
    assert not any(module.startswith("infrastructure") for module in service_imports)
    assert not any(module.startswith("langchain") for module in service_imports)
    assert "sqlmodel" not in service_imports

    assert "QuestionIntentProjectionService" in graph_source
    assert "QuestionIntentProjectionData" in graph_source
    assert "def _merge_intent_parts" not in graph_source
    assert "def _valid_required_slot_types" not in graph_source
    assert "def _apply_intent_feedback" not in graph_source


def test_graph_keeps_intent_orchestration_and_candidate_mapping():
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "ThreadPoolExecutor" in graph_source
    assert "_run_intent_subtasks" in graph_source
    assert "_fallback_intent_subtask_result" in graph_source
    assert "_record_intent_subtask_trace" in graph_source
    assert "_normalize_subject_domain_output" in graph_source
    assert "_normalize_dimension_slots_payload" in graph_source
