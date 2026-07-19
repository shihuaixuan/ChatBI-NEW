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


def _function_source(tree: ast.Module, name: str) -> str:
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    return ast.unparse(function)


def test_chat_record_service_has_no_runtime_or_session_dependency():
    imports = _imports(_tree("apps/chatbi/services/chat_record_service.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.agent.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine.") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)


def test_agent_record_terminal_projection_uses_chatbi_service():
    tree = _tree("apps/agent/crud.py")
    finish_source = _function_source(tree, "finish_record")
    complete_source = _function_source(tree, "complete_record")

    assert "build_chat_record_service" in finish_source
    assert "build_chat_record_service" in complete_source
    assert ".status =" not in finish_source
    assert ".finish =" not in finish_source


def test_workflow_projector_is_generic_and_chatbi_logic_stays_in_gateway():
    projector_imports = _imports(_tree("apps/workflow_engine/api/chat_history.py"))
    service_imports = _imports(_tree("apps/workflow_engine/api/service.py"))

    assert not any(
        module.startswith("apps.")
        and not module.startswith("apps.workflow_engine.")
        for module in projector_imports
    )
    assert "apps.chatbi.workflow_gateway" in service_imports
    assert "apps.chat.models.chat_model" not in service_imports


def test_graph_chat_binding_rule_is_forwarded_through_chatbi_gateway():
    tree = _tree("apps/workflow_engine/api/service.py")
    service_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GraphApiService"
    )
    method = next(
        node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_resolve_chat_query_context"
    )
    service_source = ast.unparse(method)

    assert "ExecutionBindingService" in service_source
    assert "chat.dataset_id" in service_source
    assert "CHAT_DATASET_MISMATCH" not in service_source


def test_legacy_chat_record_finish_functions_only_forward_state_changes():
    tree = _tree("apps/chat/curd/chat.py")

    for name in ("finish_record", "save_error_message"):
        source = _function_source(tree, name)
        assert "build_chat_record_service" in source
        assert "update(ChatRecord)" not in source


def test_legacy_analysis_and_predict_record_uses_chatbi_create_service():
    tree = _tree("apps/chat/curd/chat.py")
    source = _function_source(tree, "save_analysis_predict_record")

    assert "build_chat_record_service" in source
    assert "ChatRecordCreateData" in source
    assert "record = ChatRecord()" not in source
