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
    agent_imports = _imports("apps/capabilities/question_understanding.py")
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
