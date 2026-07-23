"""历史逐批边界守卫的合并容器（R1-d）。

本文件收拢 R0 之前按批次产生的全部边界守卫；规则内容逐条保留、按来源分节。
新增守卫规则必须写入 test_structure_rules.py 的规则表，禁止在此追加或新建守卫文件。
随 R2+ 各能力重写，对应小节应改写为 test_structure_rules 的表驱动规则并删除。
"""

import ast
from pathlib import Path

# ======================================================================
# 来源：test_chat_model_compatibility_boundary.py
# ======================================================================

BACKEND_DIR__chat_model_compatibility = Path(__file__).resolve().parents[2]


def _tree__chat_model_compatibility(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__chat_model_compatibility / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__chat_model_compatibility(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_common_data_format_does_not_depend_on_chat_models():
    imports = _imports__chat_model_compatibility(_tree__chat_model_compatibility("common/utils/data_format.py"))
    schema_imports = _imports__chat_model_compatibility(_tree__chat_model_compatibility("common/utils/data_format_schema.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.chat") for module in schema_imports)


def test_legacy_chat_model_only_contains_compatibility_exports():
    tree = _tree__chat_model_compatibility("apps/chat/models/chat_model.py")

    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    assert _imports__chat_model_compatibility(tree) == {
        "apps.chatbi.models",
        "common.utils.data_format_schema",
    }


def test_chat_runtime_uses_public_axis_schema():
    api_imports = _imports__chat_model_compatibility(
        _tree__chat_model_compatibility("apps/chatbi/api/conversations.py")
    )
    task_imports = _imports__chat_model_compatibility(_tree__chat_model_compatibility("apps/chatbi/api/legacy_chat_flow.py"))

    assert "common.utils.data_format_schema" in api_imports
    assert "common.utils.data_format_schema" in task_imports


# ======================================================================
# 来源：test_chatbi_analysis_prediction_boundary.py
# ======================================================================

BACKEND_DIR__analysis_prediction = Path(__file__).resolve().parents[2]


def _tree__analysis_prediction(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__analysis_prediction / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__analysis_prediction(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__analysis_prediction(tree: ast.Module, class_name: str, name: str) -> str:
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
    imports = _imports__analysis_prediction(
        _tree__analysis_prediction("apps/chatbi/services/generation/analysis_prediction.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_analysis_and_prediction_delegate_generation_to_chatbi():
    tree = _tree__analysis_prediction("apps/chatbi/api/legacy_chat_flow.py")

    for method_name in ("generate_analysis", "generate_predict"):
        source = _class_method_source__analysis_prediction(tree, "LLMService", method_name)
        assert "build_analysis_prediction_service" in source
        assert "service.prepare" in source
        assert "service.generate" in source
        assert "start_log" in source
        assert "end_log" in source
        assert "self.llm.stream" not in source
        assert "save_analysis_answer" not in source
        assert "save_predict_answer" not in source


def test_legacy_analysis_and_prediction_sse_contract_is_unchanged():
    tree = _tree__analysis_prediction("apps/chatbi/api/legacy_chat_flow.py")
    # R3-b 起流程拆为阶段方法；契约仍由 legacy 流程整体持有。
    source = "\n".join(
        _class_method_source__analysis_prediction(tree, "LLMService", name)
        for name in (
            "run_analysis_or_predict_task",
            "_analysis_stage",
            "_predict_stage",
            "_predict_success_output",
        )
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
    tree = _tree__analysis_prediction("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__analysis_prediction(tree)
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


# ======================================================================
# 来源：test_chatbi_answer_generation_boundary.py
# ======================================================================

BACKEND_DIR__answer_generation = Path(__file__).resolve().parents[2]


def _imports__answer_generation(relative_path: str) -> set[str]:
    tree = ast.parse((BACKEND_DIR__answer_generation / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_answer_generation_service_has_no_executor_or_framework_dependency():
    imports = _imports__answer_generation("apps/chatbi/services/generation/answer_generation.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_answer_adapter_uses_chatbi_service_and_shared_model_boundary():
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/workflow/capabilities/adapters/answer.py"
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
        BACKEND_DIR__answer_generation / "apps/chatbi/services/generation/answer_generation.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")
    schema_source = (BACKEND_DIR__answer_generation / "apps/workflow/schemas/v1.py").read_text(
        encoding="utf-8"
    )

    assert "def build_answer_generation_prompt" in service_source
    assert "def build_answer_generation_prompt" not in graph_source
    assert "class AnswerOutput(AnswerGenerationResult)" in schema_source
    assert "class AnswerGenerationResult" not in schema_source


def test_graph_keeps_answer_context_projection_and_final_composition():
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")

    assert "def build_answer_projection" in graph_source
    assert "def compose" in graph_source
    assert "ChatBIRunContext" in graph_source


def test_answer_projection_service_has_no_executor_or_framework_dependency():
    imports = _imports__answer_generation("apps/chatbi/services/generation/answer_projection.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_graph_answer_projection_only_reads_context_and_calls_chatbi_service():
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")

    assert "project_answer_context" in graph_source
    assert "AnswerProjectionData" in graph_source
    assert "project_answer_context(" in graph_source
    assert "def _project_plan" not in graph_source
    assert "def _project_execution_result" not in graph_source
    assert "def _project_validation" not in graph_source
    assert "def _project_multi_query_analysis" not in graph_source
    assert "def _results_by_role" not in graph_source
    assert "def _share_analysis" not in graph_source
    assert "def _comparison_analysis" not in graph_source
    assert "def _numeric_value" not in graph_source
    assert "def _project_error" not in graph_source


def test_final_reply_projection_service_has_no_executor_or_framework_dependency():
    imports = _imports__answer_generation("apps/chatbi/services/generation/final_reply.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_final_reply_contract_and_composition_are_owned_by_chatbi():
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/workflow/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")
    service_source = (
        BACKEND_DIR__answer_generation / "apps/chatbi/services/generation/final_reply.py"
    ).read_text(encoding="utf-8")
    schema_source = (BACKEND_DIR__answer_generation / "apps/workflow/schemas/v1.py").read_text(
        encoding="utf-8"
    )

    assert "project_final_reply" in graph_source
    assert "FinalReplyProjectionData" in graph_source
    assert "FinalReplyOutput" not in graph_source
    assert "real_chatbi_v1" not in graph_source
    assert "real_chatbi_v1" in service_source
    assert "class FinalReplyOutput(FinalReplyProjectionResult)" in schema_source


def test_agent_finish_uses_chatbi_final_reply_projection():
    agent_source = (BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/agent/tools/core.py").read_text(
        encoding="utf-8"
    )
    service_source = (
        BACKEND_DIR__answer_generation / "apps/chatbi/services/generation/final_reply.py"
    ).read_text(encoding="utf-8")

    assert "project_query_final_reply(" in agent_source
    assert "QueryFinalReplyProjectionData" in agent_source
    assert "非标准指标口径" not in agent_source
    assert "execution_required_before_finish" not in agent_source
    assert "非标准指标口径" in service_source
    assert "execution_required_before_finish" in service_source


# ======================================================================
# 来源：test_chatbi_artifact_boundary.py
# ======================================================================

BACKEND_DIR__artifact = Path(__file__).resolve().parents[2]


def _imports__artifact(relative_path: str) -> set[str]:
    path = BACKEND_DIR__artifact / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_chatbi_artifact_service_has_no_workflow_or_persistence_dependency():
    imports = _imports__artifact("apps/chatbi/services/execution/result_artifacts.py")

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.workflow_engine") for module in imports)
    assert not any(".infrastructure" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)


def test_agent_and_graph_share_chatbi_result_artifact_service():
    agent_source = (
        BACKEND_DIR__artifact / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__artifact / "apps/workflow/capabilities/adapters/sql.py"
    ).read_text(encoding="utf-8")
    execution_imports = _imports__artifact("apps/workflow/capabilities/execution.py")

    assert "result_artifact_service.save" in agent_source
    assert "result_artifact_service.save" in graph_source
    assert "apps.workflow_engine.domain.artifact" not in execution_imports


def test_chat_deletion_uses_unified_artifact_and_agent_cleanup_entries():
    source = (
        BACKEND_DIR__artifact
        / "apps/chatbi/services/conversation/deletion_service.py"
    ).read_text(encoding="utf-8")

    assert "schedule_chat_cleanup" in source
    assert "process_pending_cleanup" in source
    assert "run_cleanup" in source
    assert "WorkflowArtifactModel" not in source
    assert "AgentExecutionDeletionService" not in source


# ======================================================================
# 来源：test_chatbi_chart_generation_boundary.py
# ======================================================================

BACKEND_DIR__chart_generation = Path(__file__).resolve().parents[2]


def _tree__chart_generation(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__chart_generation / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__chart_generation(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__chart_generation(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_chart_generation_service_only_depends_on_stable_ports():
    imports = _imports__chart_generation(_tree__chart_generation("apps/chatbi/services/generation/chart_generation.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_chart_generation_delegates_to_chatbi():
    tree = _tree__chart_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__chart_generation(tree, "LLMService", "generate_chart")

    assert "build_chart_generation_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "self.llm.stream" not in source
    assert "chart_sys_question" not in source
    assert "chart_user_question" not in source
    assert "save_chart_answer" not in source


def test_legacy_chart_parser_and_writer_are_removed():
    tree = _tree__chart_generation("apps/chatbi/api/legacy_chat_flow.py")
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LLMService"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "check_save_chart" not in method_names


def test_legacy_run_task_keeps_chart_sse_contract():
    tree = _tree__chart_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = "\n".join(
        _class_method_source__chart_generation(tree, "LLMService", name)
        for name in ("run_task", "_generate_chart_stage", "_render_final_output")
    )

    for event_type in ("chart-result", "chart", "finish"):
        assert event_type in source


def test_legacy_chat_question_no_longer_owns_chart_templates():
    tree = _tree__chart_generation("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__chart_generation(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_chart.generator" not in imports
    assert "chart_sys_question" not in method_names
    assert "chart_user_question" not in method_names


# ======================================================================
# 来源：test_chatbi_conversation_boundary.py
# ======================================================================

BACKEND_DIR__conversation = Path(__file__).resolve().parents[2]


def _imports__conversation(path: Path) -> set[str]:
    modules: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_conversation_service_depends_on_ports_not_session_or_legacy_chat():
    path = BACKEND_DIR__conversation / "apps/chatbi/services/conversation/conversation_service.py"
    imports = _imports__conversation(path)

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource.") for module in imports)
    assert not any(module.startswith("apps.semantic.") for module in imports)
    assert not any(module.startswith("apps.knowledge.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine.") for module in imports)


def test_legacy_conversation_mutations_have_moved_out_of_read_projection():
    path = BACKEND_DIR__conversation / "apps/chatbi/api/legacy_read.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    for name in (
        "list_chats",
        "rename_chat_with_user",
        "delete_chat_with_user",
        "create_chat",
    ):
        assert name not in functions

    router_source = (
        BACKEND_DIR__conversation / "apps/chatbi/api/conversations.py"
    ).read_text(encoding="utf-8")
    assert "build_conversation_reader_service" in router_source
    assert "build_legacy_conversation_service" in router_source


def test_agent_conversation_owner_check_uses_chatbi_service():
    path = (
        BACKEND_DIR__conversation
        / "apps/chatbi/orchestration/agent/service.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_record_and_run"
    )
    source = ast.unparse(function)

    assert "build_conversation_reader_service" in source
    assert ".get_owned(" in source
    assert "session.get(" not in source


# ======================================================================
# 来源：test_chatbi_datasource_selection_boundary.py
# ======================================================================

BACKEND_DIR__datasource_selection = Path(__file__).resolve().parents[2]


def _tree__datasource_selection(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__datasource_selection / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__datasource_selection(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__datasource_selection(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_datasource_selection_service_only_depends_on_stable_ports():
    imports = _imports__datasource_selection(
        _tree__datasource_selection("apps/chatbi/services/planning/datasource_selection.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_legacy_datasource_selection_delegates_model_and_binding_to_chatbi():
    tree = _tree__datasource_selection("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__datasource_selection(tree, "LLMService", "select_datasource")

    assert "build_datasource_selection_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "service.bind_selection" in source
    assert "self.llm.stream" not in source
    assert "extract_nested_json" not in source
    assert "save_select_datasource_answer" not in source
    assert "_chat.datasource" not in source
    assert "_session.get(Chat" not in source


def test_legacy_datasource_selection_sse_contract_is_unchanged():
    tree = _tree__datasource_selection("apps/chatbi/api/legacy_chat_flow.py")
    source = "\n".join(
        _class_method_source__datasource_selection(tree, "LLMService", name)
        for name in ("run_task", "_resolve_datasource_stage")
    )

    assert "datasource-result" in source
    assert "datasource_name" in source
    assert "engine_type" in source


def test_legacy_chat_question_no_longer_owns_datasource_selection_template():
    tree = _tree__datasource_selection("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__datasource_selection(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.select_datasource.generator" not in imports
    assert "datasource_sys_question" not in method_names
    assert "datasource_user_question" not in method_names


# ======================================================================
# 来源：test_chatbi_dynamic_sql_generation_boundary.py
# ======================================================================

BACKEND_DIR__dynamic_sql_generation = Path(__file__).resolve().parents[2]


def _tree__dynamic_sql_generation(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__dynamic_sql_generation / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__dynamic_sql_generation(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__dynamic_sql_generation(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_dynamic_sql_generation_service_only_depends_on_stable_ports():
    imports = _imports__dynamic_sql_generation(
        _tree__dynamic_sql_generation("apps/chatbi/services/generation/dynamic_sql_generation.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_dynamic_sql_generation_delegates_to_chatbi():
    tree = _tree__dynamic_sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__dynamic_sql_generation(tree, "LLMService", "generate_with_sub_sql")

    assert "build_dynamic_sql_generation_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "self.llm.stream" not in source
    assert "dynamic_sys_question" not in source
    assert "dynamic_user_question" not in source
    assert "check_save_sql" not in source


def test_legacy_dynamic_sql_mapping_keeps_placeholder_replacement_contract():
    tree = _tree__dynamic_sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__dynamic_sql_generation(
        tree,
        "LLMService",
        "generate_assistant_dynamic_sql",
    )

    assert "DynamicSQLSubqueryMapping" in source
    assert "dynamic_subsql_prefix" in source
    assert "sqlbot_temp_sql_text" in source


def test_legacy_chat_question_no_longer_owns_dynamic_sql_templates():
    tree = _tree__dynamic_sql_generation("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__dynamic_sql_generation(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_dynamic.generator" not in imports
    assert "dynamic_sys_question" not in method_names
    assert "dynamic_user_question" not in method_names


# ======================================================================
# 来源：test_chatbi_generation_context_boundary.py
# ======================================================================

BACKEND_DIR__generation_context = Path(__file__).resolve().parents[2]


def _imports__generation_context(relative_path: str) -> set[str]:
    tree = ast.parse((BACKEND_DIR__generation_context / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_generation_context_uses_other_domains_public_services_only():
    imports = _imports__generation_context("apps/chatbi/services/generation/context/knowledge.py")

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
    imports = _imports__generation_context(
        "apps/chatbi/services/generation/context/runtime_settings.py"
    )

    assert not any(
        module.startswith(("apps.system", "sqlmodel", "sqlbot_xpack"))
        for module in imports
    )


def test_generation_schema_context_uses_public_domain_services_only():
    imports = _imports__generation_context(
        "apps/chatbi/services/generation/context/schema_context.py"
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
    imports = _imports__generation_context(
        "apps/chatbi/services/planning/datasource_candidates.py"
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
    imports = _imports__generation_context("apps/chatbi/api/legacy_external_datasource.py")

    assert "apps.datasource.crud.datasource" not in imports
    assert "apps.datasource.embedding.ds_embedding" not in imports
    assert "get_assistant_ds" not in (
        BACKEND_DIR__generation_context / "apps/chatbi/api/legacy_external_datasource.py"
    ).read_text(encoding="utf-8")
    assert "get_assistant_ds" not in (
        BACKEND_DIR__generation_context / "apps/chatbi/api/legacy_chat_flow.py"
    ).read_text(encoding="utf-8")
    assert not (
        BACKEND_DIR__generation_context / "apps/datasource/embedding/ds_embedding.py"
    ).exists()


def test_legacy_llm_keeps_unmigrated_dependencies_in_legacy_module():
    imports = _imports__generation_context("apps/chatbi/api/legacy_chat_flow.py")

    assert "apps.chatbi.api.legacy_external_datasource" in imports
    assert "apps.ai_model.runtime" in imports
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
    assert not (BACKEND_DIR__generation_context / "apps/chat/services/term_context.py").exists()


def test_top_level_infrastructure_package_has_been_removed():
    assert not (BACKEND_DIR__generation_context / "infrastructure").exists()


# ======================================================================
# 来源：test_chatbi_generation_context_scope_boundary.py
# ======================================================================

BACKEND_DIR__generation_context_scope = Path(__file__).resolve().parents[2]


def _tree__generation_context_scope(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__generation_context_scope / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__generation_context_scope(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__generation_context_scope(tree: ast.Module, class_name: str, name: str) -> str:
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
    imports = _imports__generation_context_scope(
        _tree__generation_context_scope("apps/chatbi/services/generation/context/scope.py")
    )

    assert imports == {"apps.chatbi.models.dto.generation_context"}


def test_legacy_prompt_and_example_filters_use_shared_scope():
    tree = _tree__generation_context_scope("apps/chatbi/api/legacy_chat_flow.py")

    for method_name in ("filter_custom_prompts", "filter_training_template"):
        source = _class_method_source__generation_context_scope(tree, "LLMService", method_name)
        assert "resolve_generation_context_scope" in source
        assert "current_assistant.type" not in source
        assert "calculate_oid" not in source
        assert "calculate_ds_id" not in source


def test_legacy_scope_method_only_adapts_context_to_chatbi():
    source = _class_method_source__generation_context_scope(
        _tree__generation_context_scope("apps/chatbi/api/legacy_chat_flow.py"),
        "LLMService",
        "resolve_generation_context_scope",
    )

    assert "resolve_generation_scope" in source
    assert "GenerationContextScopeData" in source
    assert "GenerationAssistantContext" in source
    assert "assistant_type ==" not in source
    assert "assistant_type !=" not in source


# ======================================================================
# 来源：test_chatbi_generation_custom_prompt_boundary.py
# ======================================================================

BACKEND_DIR__generation_custom_prompt = Path(__file__).resolve().parents[2]


def _tree__generation_custom_prompt(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__generation_custom_prompt / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__generation_custom_prompt(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__generation_custom_prompt(tree: ast.Module, class_name: str, name: str) -> str:
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
    imports = _imports__generation_custom_prompt(
        _tree__generation_custom_prompt("apps/chatbi/services/generation/context/knowledge.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("sqlbot_xpack") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)


def test_xpack_custom_prompt_dependency_is_owned_by_chatbi_adapter():
    imports = _imports__generation_custom_prompt(
        _tree__generation_custom_prompt("apps/chatbi/adapters/generation_custom_prompt.py")
    )

    assert "sqlbot_xpack.custom_prompt.curd.custom_prompt" in imports
    assert "sqlbot_xpack.custom_prompt.models.custom_prompt_model" in imports
    assert "sqlbot_xpack.license.license_manage" in imports


def test_legacy_llm_uses_chatbi_custom_prompt_contract():
    tree = _tree__generation_custom_prompt("apps/chatbi/api/legacy_chat_flow.py")
    imports = _imports__generation_custom_prompt(tree)
    source = _class_method_source__generation_custom_prompt(tree, "LLMService", "filter_custom_prompts")

    assert not any(
        module.startswith("sqlbot_xpack.custom_prompt") for module in imports
    )
    assert "sqlbot_xpack.license.license_manage" not in imports
    assert "build_generation_custom_prompt_service" in source
    assert "GenerationCustomPromptQuery" in source
    assert "find_custom_prompts" not in source
    assert "SQLBotLicenseUtil" not in source


# ======================================================================
# 来源：test_chatbi_generation_history_boundary.py
# ======================================================================

BACKEND_DIR__generation_history = Path(__file__).resolve().parents[2]


def _tree__generation_history(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__generation_history / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__generation_history(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__generation_history(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_generation_history_projection_only_depends_on_chatbi_dto():
    imports = _imports__generation_history(
        _tree__generation_history("apps/chatbi/services/generation/context/history.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_legacy_init_messages_delegates_history_projection_to_chatbi():
    tree = _tree__generation_history("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__generation_history(tree, "LLMService", "init_messages")

    assert "project_generation_history" in source
    assert "GenerationHistoryProjectionData" in source
    assert "GenerationHistoryLog" in source
    assert "get_last_conversation_rounds" not in source
    assert "sqlbot_system" not in source


def test_legacy_history_round_helper_is_removed():
    tree = _tree__generation_history("apps/chatbi/api/legacy_chat_flow.py")
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    assert "get_last_conversation_rounds" not in function_names


# ======================================================================
# 来源：test_chatbi_permission_sql_generation_boundary.py
# ======================================================================

BACKEND_DIR__permission_sql_generation = Path(__file__).resolve().parents[2]


def _tree__permission_sql_generation(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__permission_sql_generation / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__permission_sql_generation(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__permission_sql_generation(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_permission_sql_generation_service_only_depends_on_stable_ports():
    imports = _imports__permission_sql_generation(
        _tree__permission_sql_generation("apps/chatbi/services/generation/permission_sql_generation.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_permission_sql_generation_delegates_to_chatbi():
    tree = _tree__permission_sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__permission_sql_generation(tree, "LLMService", "build_table_filter")

    assert "build_permission_sql_generation_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "self.llm.stream" not in source
    assert "filter_sys_question" not in source
    assert "filter_user_question" not in source


def test_legacy_llm_service_no_longer_owns_sql_response_parser():
    tree = _tree__permission_sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LLMService"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }
    class_source = ast.unparse(class_node)

    assert "check_sql" not in method_names
    assert "check_save_sql" not in method_names
    assert "self.llm.stream" not in class_source


def test_legacy_permission_sources_use_stable_filter_dto():
    tree = _tree__permission_sql_generation("apps/chatbi/api/legacy_chat_flow.py")

    for method_name in ("generate_filter", "generate_assistant_filter"):
        source = _class_method_source__permission_sql_generation(tree, "LLMService", method_name)
        assert "PermissionSQLFilter" in source
        assert "build_table_filter" in source


def test_legacy_chat_question_no_longer_owns_permission_sql_templates():
    tree = _tree__permission_sql_generation("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__permission_sql_generation(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.filter.generator" not in imports
    assert "filter_sys_question" not in method_names
    assert "filter_user_question" not in method_names


# ======================================================================
# 来源：test_chatbi_query_boundary.py
# ======================================================================

BACKEND_DIR__query = Path(__file__).resolve().parents[2]


def _imports__query(relative_path: str) -> set[str]:
    path = BACKEND_DIR__query / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_agent_sql_tools_only_use_chatbi_query_service():
    imports = _imports__query("apps/chatbi/orchestration/agent/tools/core.py")

    assert "apps.chatbi.services" in imports
    assert "apps.capabilities.sql.executor" not in imports
    assert "apps.capabilities.sql.permission" not in imports
    assert "apps.chatbi.services.execution.sql_validator" not in imports


def test_graph_sql_adapter_does_not_maintain_second_execution_chain():
    path = BACKEND_DIR__query / "apps/workflow/capabilities/adapters/sql.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports__query("apps/workflow/capabilities/adapters/sql.py")

    assert "apps.chatbi.services" in imports
    assert "self._execute_tool" not in source
    assert "self._validate_tool" not in source
    assert "self._permission_adapter.apply" not in source


def test_chatbi_query_service_has_no_session_or_datasource_dependency():
    imports = _imports__query("apps/chatbi/services/execution/guarded_query_service.py")

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.datasource") for module in imports)


def test_agent_and_graph_share_chatbi_semantic_query_service():
    agent_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__query / "apps/workflow/capabilities/adapters/sql.py"
    ).read_text(encoding="utf-8")

    assert "semantic_query_service.compile" in agent_source
    assert "compile_semantic_sql(" not in agent_source
    assert "_compile_semantic_query" in graph_source
    assert "self._compiler.compile" not in graph_source


def test_chatbi_semantic_query_service_has_no_session_or_repository_dependency():
    imports = _imports__query("apps/chatbi/services/planning/semantic_compilation.py")

    assert "sqlmodel" not in imports
    assert not any(".repository" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)


def test_agent_semantic_retrieval_and_physical_schema_use_chatbi_services():
    imports = _imports__query("apps/chatbi/orchestration/agent/tools/core.py")
    source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")

    assert "apps.capabilities.semantic.retrieval" not in imports
    assert not any(module.startswith("apps.datasource") for module in imports)
    assert "semantic_retrieval_service.retrieve_for_agent" in source
    assert "physical_schema_service.get" in source


def test_graph_semantic_retrieval_uses_chatbi_service():
    imports = _imports__query("apps/workflow/capabilities/adapters/knowledge.py")
    source = (
        BACKEND_DIR__query / "apps/workflow/capabilities/adapters/knowledge.py"
    ).read_text(encoding="utf-8")

    assert "apps.chatbi.services" in imports
    assert "build_semantic_binding_request" not in source
    assert "semantic_retrieval_service.retrieve" in source


def test_chatbi_retrieval_and_schema_services_have_no_runtime_dependency():
    retrieval_imports = _imports__query(
        "apps/chatbi/services/planning/semantic_retrieval.py"
    )
    schema_imports = _imports__query("apps/chatbi/services/planning/physical_schema.py")

    for imports in (retrieval_imports, schema_imports):
        assert "sqlmodel" not in imports
        assert not any(".repository" in module for module in imports)
        assert not any(".models.orm" in module for module in imports)


def test_agent_dataset_context_does_not_resolve_arbitrary_dataset_by_datasource():
    imports = _imports__query("apps/chatbi/orchestration/agent/tools/core.py")
    source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")

    assert "apps.capabilities.semantic.compile" not in imports
    assert "resolve_dataset_by_datasource" not in source


def test_execution_binding_service_has_no_runtime_or_repository_dependency():
    imports = _imports__query("apps/chatbi/services/planning/execution_binding.py")

    assert "sqlmodel" not in imports
    assert not any(".repository" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)


# ======================================================================
# 来源：test_chatbi_query_result_projection_boundary.py
# ======================================================================

BACKEND_DIR__query_result_projection = Path(__file__).resolve().parents[2]


def _tree__query_result_projection(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__query_result_projection / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__query_result_projection(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__query_result_projection(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_query_result_projection_service_only_depends_on_stable_boundaries():
    imports = _imports__query_result_projection(
        _tree__query_result_projection("apps/chatbi/services/execution/result_projection.py")
    )

    assert "sqlmodel" not in imports
    assert "common.utils.data_format" not in imports
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource") for module in imports)
    assert not any(module.startswith("apps.chatbi.models.orm") for module in imports)


def test_legacy_chat_run_task_delegates_result_projection_and_owns_transaction():
    tree = _tree__query_result_projection("apps/chatbi/api/legacy_chat_flow.py")
    source = "\n".join(
        _class_method_source__query_result_projection(tree, "LLMService", name)
        for name in ("run_task", "_execute_sql_stage")
    )

    assert "build_query_result_projection_service" in source
    assert "QueryResultProjectionData" in source
    assert ".project" in source
    assert "_session.commit()" in source
    assert "_session.rollback()" in source
    assert "convert_large_numbers_in_object_array" not in source
    assert "normalize_qualified_sql_column_keys_in_object_array" not in source
    assert "save_sql_data" not in source


def test_legacy_chat_result_writer_is_removed():
    tree = _tree__query_result_projection("apps/chatbi/api/legacy_chat_flow.py")
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LLMService"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }
    source = ast.unparse(tree)

    assert "save_sql_data" not in method_names
    assert "save_sql_exec_data" not in source
    assert "prepare_for_orjson" not in source


def test_legacy_chat_result_flow_keeps_existing_output_contract():
    source = "\n".join(
        _class_method_source__query_result_projection(
            _tree__query_result_projection("apps/chatbi/api/legacy_chat_flow.py"),
            "LLMService",
            name,
        )
        for name in (
            "run_task",
            "_finish_query_data_stage",
            "_generate_chart_stage",
            "_render_final_output",
        )
    )

    assert "execute-success" in source
    assert "sql-data" in source
    assert "to_markdown" in source
    assert "generate_chart" in source
    assert "request_picture" in source


# ======================================================================
# 来源：test_chatbi_question_understanding_boundary.py
# ======================================================================

BACKEND_DIR__question_understanding = Path(__file__).resolve().parents[2]

UNDERSTANDING__question_understanding = "apps/chatbi/services/understanding"


def _imports__question_understanding(relative_path: str) -> set[str]:
    tree = ast.parse((BACKEND_DIR__question_understanding / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_agent_and_graph_share_question_understanding_validation_rules():
    agent_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/understanding_service.py")
    graph_contract_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/graph_contracts.py")

    validation_module = "apps.chatbi.services.understanding.validation"
    assert validation_module in agent_imports
    assert validation_module in graph_contract_imports


def test_graph_intent_adapter_does_not_reimplement_dimension_time_rule():
    question_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")
    validation_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/intent_validation.py"
    ).read_text(encoding="utf-8")

    assert "def _is_time_expression" not in question_source
    assert "_dimension_time_value_violations" not in validation_source


def test_question_validation_rules_have_no_executor_or_model_dependency():
    imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/validation.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert "sqlmodel" not in imports


def test_structured_model_service_has_no_executor_or_model_framework_dependency():
    imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/model_invocation.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_agent_and_graph_share_structured_model_service():
    agent_source = (
        BACKEND_DIR__question_understanding / f"{UNDERSTANDING__question_understanding}/understanding_service.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    for source in (agent_source, graph_source):
        assert "StructuredModelService" in source
        assert "QuestionModelInvocationData" in source
        assert "LLMFactory" not in source
        assert "get_default_config" not in source
        assert "SystemMessage" not in source
        assert "HumanMessage" not in source

    assert "QuestionModelJSONMode.EXTRACT_OBJECT" in graph_source
    assert "QuestionModelJSONMode.EXTRACT_OBJECT" not in agent_source
    assert "build_question_model_service" not in agent_source
    assert "infrastructure.question_model" not in agent_source


def test_default_question_model_client_stays_in_chatbi_adapter():
    adapter_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/adapters/question_model.py"
    ).read_text(encoding="utf-8")
    agent_source = (
        BACKEND_DIR__question_understanding / f"{UNDERSTANDING__question_understanding}/understanding_service.py"
    ).read_text(encoding="utf-8")
    composition_source = (BACKEND_DIR__question_understanding / "apps/chatbi/composition.py").read_text(
        encoding="utf-8"
    )
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "class LangChainQuestionModelClient" in adapter_source
    assert "LLMFactory" in adapter_source
    assert "DefaultQuestionUnderstandingModelClient" not in agent_source
    assert "DefaultQuestionClassificationModelClient" not in graph_source
    assert "build_question_model_service" in composition_source


def test_question_understanding_dtos_are_owned_by_chatbi():
    agent_source = (
        BACKEND_DIR__question_understanding / "apps/capabilities/question_understanding.py"
    ).read_text(encoding="utf-8")
    graph_source = (BACKEND_DIR__question_understanding / "apps/workflow/schemas/v1.py").read_text(
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

    assert "QuestionClassificationOutputBase" in graph_source
    assert "QuestionRewriteProjectionOutput" in graph_source
    assert "NaturalLanguageIntentOutputBase" in graph_source


def test_question_understanding_prompt_rules_are_owned_by_chatbi():
    prompt_import = "apps.chatbi.services.understanding.prompts"
    agent_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/understanding_service.py")
    graph_imports = _imports__question_understanding("apps/workflow/capabilities/adapters/question.py")
    prompt_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/prompts.py")

    assert prompt_import in agent_imports
    assert prompt_import in graph_imports
    assert not any(module.startswith("apps.agent") for module in prompt_imports)
    assert not any(module.startswith("apps.workflow") for module in prompt_imports)
    assert not any(module.startswith("apps.ai_model") for module in prompt_imports)
    assert not any(module.startswith("langchain") for module in prompt_imports)
    assert "sqlmodel" not in prompt_imports


def test_question_understanding_service_has_no_executor_or_infrastructure_dependency():
    imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/understanding_service.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_old_question_understanding_path_only_reexports_chatbi_objects():
    compatibility_path = "apps/capabilities/question_understanding.py"
    compatibility_source = (BACKEND_DIR__question_understanding / compatibility_path).read_text(encoding="utf-8")
    compatibility_imports = _imports__question_understanding(compatibility_path)
    agent_loop_source = (BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/agent/loop.py").read_text(encoding="utf-8")

    assert compatibility_imports == {
        "apps.chatbi.models.dto.question_understanding",
        "apps.chatbi.services.understanding.understanding_service",
    }
    assert "class QuestionUnderstandingService" not in compatibility_source
    assert "def apply_question_understanding_clarification" not in compatibility_source
    assert "apps.capabilities.question_understanding" not in agent_loop_source


def test_graph_intent_projection_rules_are_owned_by_chatbi():
    service_path = f"{UNDERSTANDING__question_understanding}/intent_projection.py"
    service_imports = _imports__question_understanding(service_path)
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert not any(module.startswith("apps.agent") for module in service_imports)
    assert not any(module.startswith("apps.workflow") for module in service_imports)
    assert not any(module.startswith("apps.ai_model") for module in service_imports)
    assert not any(module.startswith("infrastructure") for module in service_imports)
    assert not any(module.startswith("langchain") for module in service_imports)
    assert "sqlmodel" not in service_imports

    assert "intent_projection.project_question_intent" in graph_source
    assert "QuestionIntentProjectionData" in graph_source
    assert "def _merge_intent_parts" not in graph_source
    assert "def _valid_required_slot_types" not in graph_source
    assert "def _apply_intent_feedback" not in graph_source


def test_graph_keeps_intent_orchestration_and_candidate_mapping():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "ThreadPoolExecutor" in graph_source
    assert "_run_intent_subtasks" in graph_source
    assert "_fallback_intent_subtask_result" in graph_source
    assert "_record_intent_subtask_trace" in graph_source
    assert "_normalize_subject_domain_output" in graph_source
    assert "_normalize_dimension_slots_payload" in graph_source


def test_graph_contracts_have_no_graph_or_framework_dependency():
    imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/graph_contracts.py")

    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert not any(module.startswith("apps.ai_model") for module in imports)
    assert not any(module.startswith("infrastructure") for module in imports)
    assert not any(module.startswith("langchain") for module in imports)
    assert "sqlmodel" not in imports


def test_graph_uses_chatbi_intent_validation_directly():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "graph_contracts.validate_intent" in graph_source
    assert "apps.workflow.capabilities.adapters.intent_validation" not in graph_source


def test_old_graph_intent_validation_path_only_reexports_chatbi_contract():
    compatibility_path = "apps/workflow/capabilities/adapters/intent_validation.py"
    compatibility_source = (BACKEND_DIR__question_understanding / compatibility_path).read_text(encoding="utf-8")

    assert _imports__question_understanding(compatibility_path) == {
        "apps.chatbi.services.understanding.graph_contracts"
    }
    assert "class IntentPostProcessor" not in compatibility_source
    assert "def validate" not in compatibility_source


def test_graph_keeps_intent_model_retry_orchestration():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "def _recognize_subtask" in graph_source
    assert "for retry_count in range" in graph_source
    assert "def _run_intent_subtasks" in graph_source


def test_question_input_projection_rules_are_owned_by_chatbi():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "graph_contracts.classification_precondition" in graph_source
    assert "graph_contracts.project_classification" in graph_source
    assert "graph_contracts.project_rewrite" in graph_source
    assert "def _dump" not in graph_source
    assert "def _rewrite_dump" not in graph_source
    assert "def _normalize_rewrite_output" not in graph_source
    assert "def _rewrite_fallback" not in graph_source


def test_graph_keeps_question_model_error_and_rewrite_fallback_orchestration():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "CLASSIFICATION_MODEL_CALL_FAILED" in graph_source
    assert "CLASSIFICATION_MODEL_OUTPUT_INVALID" in graph_source
    assert "except Exception:" in graph_source
    assert "graph_contracts.fallback_rewrite" in graph_source


def test_question_intent_fallback_rules_are_owned_by_chatbi():
    service_path = f"{UNDERSTANDING__question_understanding}/intent_fallback.py"
    service_imports = _imports__question_understanding(service_path)
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert not any(module.startswith("apps.agent") for module in service_imports)
    assert not any(module.startswith("apps.workflow") for module in service_imports)
    assert not any(module.startswith("apps.ai_model") for module in service_imports)
    assert not any(module.startswith("infrastructure") for module in service_imports)
    assert not any(module.startswith("langchain") for module in service_imports)
    assert "sqlmodel" not in service_imports

    assert "QuestionIntentFallbackService" in graph_source
    assert "_intent_fallback_service.infer" in graph_source
    assert "def _intent_dump" not in graph_source
    assert "def _intent_fallback" not in graph_source
    assert "def _dimension_slots_from_question" not in graph_source
    assert "def _extract_metric_mentions" not in graph_source
    assert "def _extract_dimension_mentions" not in graph_source
    assert "def _extract_time_mentions" not in graph_source
    assert "def _infer_time_grain" not in graph_source
    assert "def _infer_order_direction" not in graph_source
    assert "def _infer_limit" not in graph_source


def test_graph_keeps_intent_fallback_trigger_and_subtask_projection():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/workflow/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "def _intent_subtask_fallback_payloads" in graph_source
    assert "def _fallback_intent_subtask_result" in graph_source
    assert "exception_fallback" in graph_source
    assert "timeout_fallback" in graph_source
    assert "_record_intent_subtask_trace" in graph_source


# ======================================================================
# 来源：test_chatbi_recommended_question_boundary.py
# ======================================================================

BACKEND_DIR__recommended_question = Path(__file__).resolve().parents[2]


def _tree__recommended_question(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__recommended_question / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__recommended_question(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__recommended_question(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_recommended_question_service_only_depends_on_stable_ports():
    imports = _imports__recommended_question(
        _tree__recommended_question("apps/chatbi/services/generation/recommended_questions.py")
    )

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_recommendation_task_only_keeps_schema_log_and_sse_projection():
    tree = _tree__recommended_question("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__recommended_question(
        tree,
        "LLMService",
        "generate_recommend_questions_task",
    )

    assert "build_recommended_question_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "guess_sys_question" not in source
    assert "guess_user_question" not in source
    assert "get_old_questions" not in source
    assert "save_recommend_question_answer" not in source
    assert "self.llm.stream" not in source


def test_sql_model_adapter_uses_shared_model_stream_parser():
    # R2 起共享流解析统一收敛在 chatbi/adapters/langchain.py 的共享客户端。
    adapter_tree = _tree__recommended_question("apps/chatbi/adapters/langchain.py")
    adapter_imports = _imports__recommended_question(adapter_tree)
    legacy_tree = _tree__recommended_question("apps/chatbi/api/legacy_chat_flow.py")

    assert "apps.ai_model.streaming" in adapter_imports
    assert "apps.ai_model.streaming" not in _imports__recommended_question(legacy_tree)
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "process_stream"
        for node in adapter_tree.body
    )


def test_legacy_chat_question_no_longer_owns_recommendation_templates():
    tree = _tree__recommended_question("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__recommended_question(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_guess_question.generator" not in imports
    assert "guess_sys_question" not in method_names
    assert "guess_user_question" not in method_names


# ======================================================================
# 来源：test_chatbi_record_boundary.py
# ======================================================================

BACKEND_DIR__record = Path(__file__).resolve().parents[2]


def _tree__record(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__record / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__record(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _function_source__record(tree: ast.Module, name: str) -> str:
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    return ast.unparse(function)


def _class_method_source__record(tree: ast.Module, class_name: str, method_name: str) -> str:
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
    imports = _imports__record(_tree__record("apps/chatbi/services/conversation/chat_record_service.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.agent.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine.") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)


def test_chat_history_dto_has_no_legacy_chat_or_framework_dependency():
    imports = _imports__record(_tree__record("apps/chatbi/models/dto/chat_history.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_legacy_chat_history_models_are_only_compatibility_exports():
    tree = _tree__record("apps/chat/models/chat_model.py")
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "ChatRecordResult" not in class_names
    assert "ChatLogHistoryItem" not in class_names
    assert "ChatLogHistory" not in class_names


def test_legacy_query_dto_has_no_transport_or_model_framework_dependency():
    imports = _imports__record(_tree__record("apps/chatbi/models/dto/legacy_query.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_legacy_chat_model_only_keeps_query_context_compatibility_exports():
    tree = _tree__record("apps/chat/models/chat_model.py")
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
    tree = _tree__record("apps/chatbi/orchestration/agent/loop.py")
    loop_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop"
    )
    finish_source = ast.unparse(
        next(
            node
            for node in loop_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_finish"
        )
    )
    fail_source = ast.unparse(
        next(
            node
            for node in loop_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_fail"
        )
    )

    assert "self.record_service.transition" in finish_source
    assert "self.record_service.transition" in fail_source
    assert ".status =" not in finish_source
    assert ".finish =" not in finish_source


def test_workflow_projector_is_generic_and_chatbi_logic_stays_in_gateway():
    projector_imports = _imports__record(_tree__record("apps/workflow_engine/api/chat_history.py"))
    service_imports = _imports__record(_tree__record("apps/workflow_engine/api/service.py"))

    assert not any(
        module.startswith("apps.")
        and not module.startswith("apps.workflow_engine.")
        for module in projector_imports
    )
    assert "apps.chatbi.workflow_gateway" in service_imports
    assert "apps.chat.models.chat_model" not in service_imports


def test_graph_chat_binding_rule_is_forwarded_through_chatbi_gateway():
    tree = _tree__record("apps/workflow_engine/api/service.py")
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

    assert "resolve_execution_binding" in service_source
    assert "chat.dataset_id" in service_source
    assert "CHAT_DATASET_MISMATCH" not in service_source


def _module_function_names__record(tree):
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_legacy_chat_record_finish_functions_only_forward_state_changes():
    # R3-c1：写侧转发已删除，终态写入由 llm.py 直调 ChatRecordService。
    names = _module_function_names__record(_tree__record("apps/chatbi/api/legacy_read.py"))
    assert "finish_record" not in names
    assert "save_error_message" not in names

    llm_tree = _tree__record("apps/chatbi/api/legacy_chat_flow.py")
    for method in ("save_error", "finish"):
        source = _class_method_source__record(llm_tree, "LLMService", method)
        assert "build_chat_record_service" in source
        assert "transition_by_id" in source
        assert "update(ChatRecord)" not in source


def test_legacy_chat_run_does_not_finish_after_failure():
    source = _class_method_source__record(
        _tree__record("apps/chatbi/api/legacy_chat_flow.py"),
        "LLMService",
        "run_task",
    )

    assert "run_failed = True" in source
    assert "finalize_legacy_run(_session, run_failed, self.finish)" in source


def test_legacy_analysis_and_predict_record_uses_chatbi_create_service():
    names = _module_function_names__record(_tree__record("apps/chatbi/api/legacy_read.py"))
    assert "save_analysis_predict_record" not in names

    source = _class_method_source__record(
        _tree__record("apps/chatbi/api/legacy_chat_flow.py"),
        "LLMService",
        "run_analysis_or_predict_task_async",
    )
    assert "build_chat_record_service" in source
    assert "create_auxiliary" in source
    assert ".analysis_record_id =" not in source
    assert ".predict_record_id =" not in source


def test_legacy_core_result_writes_forward_to_chatbi_service():
    names = _module_function_names__record(_tree__record("apps/chatbi/api/legacy_read.py"))
    for name in (
        "save_sql_answer",
        "save_sql",
        "save_chart_answer",
        "save_chart",
        "save_sql_exec_data",
    ):
        assert name not in names

    source = _class_method_source__record(
        _tree__record("apps/chatbi/api/legacy_chat_flow.py"),
        "LLMService",
        "_save_record_sql",
    )
    assert "project_result_by_id" in source
    assert "update(ChatRecord)" not in source


def test_legacy_auxiliary_result_writes_forward_to_chatbi_service():
    names = _module_function_names__record(_tree__record("apps/chatbi/api/legacy_read.py"))
    for name in (
        "save_analysis_answer",
        "save_predict_answer",
        "save_select_datasource_answer",
        "save_predict_data",
        "save_recommend_question_answer",
    ):
        assert name not in names

    source = _class_method_source__record(
        _tree__record("apps/chatbi/api/legacy_chat_flow.py"),
        "LLMService",
        "check_save_predict_data",
    )
    assert "project_auxiliary_by_id" in source
    assert "update(ChatRecord)" not in source


def test_chat_record_service_owns_final_result_size_policy():
    service_source = (
        BACKEND_DIR__record / "apps/chatbi/services/conversation/chat_record_service.py"
    ).read_text(encoding="utf-8")
    agent_loop_source = (
        BACKEND_DIR__record / "apps/chatbi/orchestration/agent/loop.py"
    ).read_text(encoding="utf-8")

    assert "ChatRecordResultLimits" in service_source
    assert "CHAT_RECORD_DATA_TOO_LARGE" in service_source
    assert 'record_payload["artifact_ref"]' in agent_loop_source


# ======================================================================
# 来源：test_chatbi_sql_generation_boundary.py
# ======================================================================

BACKEND_DIR__sql_generation = Path(__file__).resolve().parents[2]


def _tree__sql_generation(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__sql_generation / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__sql_generation(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__sql_generation(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_sql_generation_service_only_depends_on_stable_ports():
    imports = _imports__sql_generation(_tree__sql_generation("apps/chatbi/services/generation/sql_generation.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("langchain") for module in imports)
    assert not any(module.startswith("apps.template") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.workflow_engine") for module in imports)


def test_legacy_main_sql_generation_delegates_to_chatbi():
    tree = _tree__sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__sql_generation(tree, "LLMService", "generate_sql")

    assert "build_sql_generation_service" in source
    assert "service.prepare" in source
    assert "service.generate" in source
    assert "start_log" in source
    assert "end_log" in source
    assert "self.llm.stream" not in source
    assert "sql_sys_question" not in source
    assert "sql_user_question" not in source
    assert "save_sql_answer" not in source


def test_legacy_main_sql_parsing_helpers_are_removed():
    tree = _tree__sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LLMService"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "get_chart_type_from_sql_answer" not in method_names
    assert "get_brief_from_sql_answer" not in method_names


def test_legacy_run_task_keeps_sql_sse_contract():
    tree = _tree__sql_generation("apps/chatbi/api/legacy_chat_flow.py")
    source = "\n".join(
        _class_method_source__sql_generation(tree, "LLMService", name)
        for name in ("run_task", "_generate_sql_stage", "_emit_sql")
    )

    for event_type in ("sql-result", "sql", "finish"):
        assert event_type in source


def test_legacy_chat_question_no_longer_owns_sql_templates():
    tree = _tree__sql_generation("apps/chatbi/models/dto/legacy_query.py")
    imports = _imports__sql_generation(tree)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AiModelQuestion"
    )
    method_names = {
        node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
    }

    assert "apps.template.generate_sql.generator" not in imports
    assert "sql_sys_question" not in method_names
    assert "sql_user_question" not in method_names


# ======================================================================
# 来源：test_legacy_chat_llm_adapter_boundary.py
# ======================================================================

BACKEND_DIR__legacy_chat_llm_adapter = Path(__file__).resolve().parents[2]


def _tree__legacy_chat_llm_adapter(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__legacy_chat_llm_adapter / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__legacy_chat_llm_adapter(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_legacy_llm_adapter_only_depends_on_serialization_library():
    imports = _imports__legacy_chat_llm_adapter(_tree__legacy_chat_llm_adapter("apps/chatbi/api/legacy_sse.py"))

    assert imports == {"collections.abc", "typing", "orjson"}


def test_legacy_chat_streams_use_shared_sse_encoder():
    llm_source = (BACKEND_DIR__legacy_chat_llm_adapter / "apps/chatbi/api/legacy_chat_flow.py").read_text(encoding="utf-8")
    api_source = (
        BACKEND_DIR__legacy_chat_llm_adapter / "apps/chatbi/api/queries.py"
    ).read_text(encoding="utf-8")

    assert "from apps.chatbi.api.legacy_sse import" in llm_source
    assert "from apps.chatbi.api.legacy_sse import encode_sse_event" in api_source
    assert "'data:' + orjson.dumps" not in llm_source
    assert "'data:' + orjson.dumps" not in api_source


def test_legacy_llm_prompt_logs_use_shared_adapter():
    source = (BACKEND_DIR__legacy_chat_llm_adapter / "apps/chatbi/api/legacy_chat_flow.py").read_text(encoding="utf-8")

    assert "build_role_prompt_log(" in source
    assert "build_context_prompt_log(" in source
    assert "'sqlbot_system': message.role == 'system'" not in source
    assert "'sqlbot_system': message.system_context" not in source


# ======================================================================
# 来源：test_legacy_chat_query_service_boundary.py
# ======================================================================

BACKEND_DIR__legacy_chat_query_service = Path(__file__).resolve().parents[2]


def _tree__legacy_chat_query_service(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__legacy_chat_query_service / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_method_source__legacy_chat_query_service(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_legacy_chat_sql_execution_delegates_to_query_service():
    tree = _tree__legacy_chat_query_service("apps/chatbi/api/legacy_chat_flow.py")
    source = _class_method_source__legacy_chat_query_service(tree, "LLMService", "execute_sql")

    assert "build_legacy_chat_query_service" in source
    assert ".execute_sql" in source
    assert "exec_sql" not in source
    assert "workspace_id=self.current_user.oid" in source
    assert "user_id=self.current_user.id" in source


def test_legacy_chat_run_task_keeps_table_scope_for_internal_datasource():
    tree = _tree__legacy_chat_query_service("apps/chatbi/api/legacy_chat_flow.py")
    source = "\n".join(
        _class_method_source__legacy_chat_query_service(tree, "LLMService", name)
        for name in ("run_task", "_execute_sql_stage")
    )

    assert (
        "allowed_tables=None if prepared['use_dynamic_ds'] else prepared['tables']"
        in source
    )
    assert "execute-success" in source
    assert "sql-data" in source


def test_connection_driver_dependency_stays_in_chatbi_adapter():
    legacy_tree = _tree__legacy_chat_query_service("apps/chatbi/api/legacy_chat_flow.py")
    adapter_tree = _tree__legacy_chat_query_service("apps/chatbi/adapters/query_execution.py")
    legacy_source = ast.unparse(legacy_tree)
    adapter_source = ast.unparse(adapter_tree)

    assert "exec_sql(" not in legacy_source
    assert "exec_sql(" in adapter_source


# ======================================================================
# 来源：test_mcp_schema_boundary.py
# ======================================================================

BACKEND_DIR__mcp_schema = Path(__file__).resolve().parents[2]


def _tree__mcp_schema(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__mcp_schema / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports__mcp_schema(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_mcp_request_schemas_do_not_depend_on_chat_or_fastapi():
    imports = _imports__mcp_schema(_tree__mcp_schema("apps/mcp/schemas.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert "fastapi" not in imports
    assert "sqlmodel" not in imports


def test_mcp_routes_use_owned_request_schemas():
    imports = _imports__mcp_schema(_tree__mcp_schema("apps/mcp/mcp.py"))

    assert "apps.mcp.schemas" in imports
    assert "apps.chat.models.chat_model" not in imports


def test_legacy_chat_model_has_no_mcp_or_fastapi_contract():
    tree = _tree__mcp_schema("apps/chat/models/chat_model.py")
    imports = _imports__mcp_schema(tree)
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "fastapi" not in imports
    assert "McpDs" not in class_names
    assert "ChatStart" not in class_names
    assert "McpQuestion" not in class_names
