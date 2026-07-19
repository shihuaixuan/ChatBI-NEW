import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _imports(relative_path: str) -> set[str]:
    path = BACKEND_DIR / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_chatbi_artifact_service_has_no_workflow_or_persistence_dependency():
    imports = _imports("apps/chatbi/services/result_artifact_service.py")

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.workflow_engine") for module in imports)
    assert not any(".infrastructure" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)


def test_agent_and_graph_share_chatbi_result_artifact_service():
    agent_source = (
        BACKEND_DIR / "apps/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR / "apps/workflow/capabilities/adapters/sql.py"
    ).read_text(encoding="utf-8")
    execution_imports = _imports("apps/workflow/capabilities/execution.py")

    assert "result_artifact_service.save" in agent_source
    assert "result_artifact_service.save" in graph_source
    assert "apps.workflow_engine.domain.artifact" not in execution_imports


def test_chat_deletion_uses_unified_artifact_and_agent_cleanup_entries():
    source = (
        BACKEND_DIR / "apps/chat/services/deletion.py"
    ).read_text(encoding="utf-8")

    assert "schedule_chat_cleanup" in source
    assert "process_pending_cleanup" in source
    assert "AgentExecutionDeletionService" in source
    assert "WorkflowArtifactModel" not in source
