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


def _class_method_source(tree: ast.Module, class_name: str, method_name: str) -> str:
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    return ast.unparse(method)


def test_chat_record_service_has_no_runtime_or_session_dependency():
    imports = _imports(_tree("apps/chatbi/services/chat_record_service.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.agent.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine.") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)


def test_chat_history_dto_has_no_legacy_chat_or_framework_dependency():
    imports = _imports(_tree("apps/chatbi/models/dto/chat_history.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_legacy_chat_history_models_are_only_compatibility_exports():
    tree = _tree("apps/chat/models/chat_model.py")
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "ChatRecordResult" not in class_names
    assert "ChatLogHistoryItem" not in class_names
    assert "ChatLogHistory" not in class_names


def test_legacy_query_dto_has_no_transport_or_model_framework_dependency():
    imports = _imports(_tree("apps/chatbi/models/dto/legacy_query.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_legacy_chat_model_only_keeps_query_context_compatibility_exports():
    tree = _tree("apps/chat/models/chat_model.py")
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "AiModelQuestion" not in class_names
    assert "ChatQuestion" not in class_names
    assert "ChatMcp" not in class_names
    assert "ExcelData" not in class_names
    assert "SystemPromptMessage" not in class_names
    assert "HumanPromptMessage" not in class_names
    assert "AIPromptMessage" not in class_names


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


def test_legacy_chat_run_does_not_finish_after_failure():
    source = _class_method_source(
        _tree("apps/chat/task/llm.py"),
        "LLMService",
        "run_task",
    )

    assert "run_failed = True" in source
    assert "finalize_legacy_run(_session, run_failed, self.finish)" in source


def test_legacy_analysis_and_predict_record_uses_chatbi_create_service():
    tree = _tree("apps/chat/curd/chat.py")
    source = _function_source(tree, "save_analysis_predict_record")

    assert "build_chat_record_service" in source
    assert "create_auxiliary" in source
    assert "record = ChatRecord()" not in source
    assert ".analysis_record_id =" not in source
    assert ".predict_record_id =" not in source
    assert "record.chart =" not in source
    assert "record.data =" not in source


def test_legacy_core_result_writes_forward_to_chatbi_service():
    tree = _tree("apps/chat/curd/chat.py")

    for name in (
        "save_sql_answer",
        "save_sql",
        "save_chart_answer",
        "save_chart",
        "save_sql_exec_data",
    ):
        source = _function_source(tree, name)
        assert "project_result_by_id" in source
        assert "update(ChatRecord)" not in source
        assert ".sql_answer =" not in source
        assert ".sql =" not in source
        assert ".chart_answer =" not in source
        assert ".chart =" not in source
        assert ".data =" not in source


def test_legacy_auxiliary_result_writes_forward_to_chatbi_service():
    tree = _tree("apps/chat/curd/chat.py")

    for name in (
        "save_analysis_answer",
        "save_predict_answer",
        "save_select_datasource_answer",
        "save_predict_data",
    ):
        source = _function_source(tree, name)
        assert "project_auxiliary_by_id" in source
        assert "update(ChatRecord)" not in source
        assert ".analysis =" not in source
        assert ".predict =" not in source
        assert ".predict_data =" not in source
        assert ".datasource_select_answer =" not in source

    recommendation_source = _function_source(
        tree,
        "save_recommend_question_answer",
    )
    assert "project_recommendation_by_id" in recommendation_source
    assert "update(ChatRecord)" not in recommendation_source
    assert "update(Chat)" not in recommendation_source


def test_chat_record_service_owns_final_result_size_policy():
    service_source = (
        BACKEND_DIR / "apps/chatbi/services/chat_record_service.py"
    ).read_text(encoding="utf-8")
    agent_loop_source = (
        BACKEND_DIR / "apps/agent/loop.py"
    ).read_text(encoding="utf-8")

    assert "ChatRecordResultLimits" in service_source
    assert "CHAT_RECORD_DATA_TOO_LARGE" in service_source
    assert 'record_payload["artifact_ref"]' in agent_loop_source
