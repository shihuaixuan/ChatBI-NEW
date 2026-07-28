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


def test_legacy_chat_model_compatibility_entry_has_been_removed():
    assert not (
        BACKEND_DIR__chat_model_compatibility / "apps/chat/models/chat_model.py"
    ).exists()


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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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
        BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/capabilities/adapters/answer.py"
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
        BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")
    schema_source = (BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/schemas/v1.py").read_text(
        encoding="utf-8"
    )

    assert "def build_answer_generation_prompt" in service_source
    assert "def build_answer_generation_prompt" not in graph_source
    assert "class AnswerOutput(AnswerGenerationResult)" in schema_source
    assert "class AnswerGenerationResult" not in schema_source


def test_graph_keeps_answer_context_projection_and_final_composition():
    graph_source = (
        BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/capabilities/adapters/answer.py"
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
        BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/capabilities/adapters/answer.py"
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
        BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/capabilities/adapters/answer.py"
    ).read_text(encoding="utf-8")
    service_source = (
        BACKEND_DIR__answer_generation / "apps/chatbi/services/generation/final_reply.py"
    ).read_text(encoding="utf-8")
    schema_source = (BACKEND_DIR__answer_generation / "apps/chatbi/orchestration/graph/schemas/v1.py").read_text(
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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)
    assert not any(".infrastructure" in module for module in imports)
    assert not any(".models.orm" in module for module in imports)


def test_agent_and_graph_share_chatbi_result_artifact_service():
    agent_source = (
        BACKEND_DIR__artifact / "apps/chatbi/orchestration/agent/tool_execution.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__artifact / "apps/chatbi/orchestration/graph/capabilities/adapters/sql.py"
    ).read_text(encoding="utf-8")
    execution_imports = _imports__artifact("apps/chatbi/orchestration/graph/capabilities/execution.py")

    assert "result_artifact_service.save" in agent_source
    assert "result_artifact_service.save" in graph_source
    assert "sqlbot_platform.workflow_engine.domain.artifact" not in execution_imports


def test_chat_deletion_uses_unified_artifact_and_agent_cleanup_entries():
    source = (
        BACKEND_DIR__artifact
        / "apps/chatbi/services/conversation/deletion_service.py"
    ).read_text(encoding="utf-8")

    assert "schedule_chat_cleanup" in source
    assert "process_pending_cleanup" in source
    assert "GraphCleanupGateway" in source
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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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
    path = BACKEND_DIR__conversation / "apps/conversation/services/conversation.py"
    imports = _imports__conversation(path)

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.chat.") for module in imports)
    assert not any(module.startswith("apps.datasource.") for module in imports)
    assert not any(module.startswith("apps.semantic.") for module in imports)
    assert not any(module.startswith("apps.knowledge.") for module in imports)
    assert not any(module.startswith("sqlbot_platform.workflow_engine.") for module in imports)


def test_legacy_conversation_mutations_have_moved_out_of_read_projection():
    # R6-b：历史读取投影模块已整体删除，读写能力由 Conversation 领域服务承接。
    assert not (
        BACKEND_DIR__conversation / "apps/chatbi/api/legacy_read.py"
    ).exists()

    router_source = (
        BACKEND_DIR__conversation / "apps/chatbi/api/conversations.py"
    ).read_text(encoding="utf-8")
    assert "build_conversation_reader_service" in router_source
    assert "build_chat_application_service" in router_source
    assert "build_legacy_conversation_service" not in router_source
    assert "legacy_composition" not in router_source


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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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


def test_public_sql_tools_only_use_datasource_query_service():
    imports = _imports__query("apps/tool/tools/datasource.py")
    core_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")

    assert "apps.datasource" in imports
    assert not any(module.startswith("apps.chatbi") for module in imports)
    assert "apps.capabilities.sql.executor" not in imports
    assert "apps.capabilities.sql.permission" not in imports
    assert "apps.chatbi.services.execution.sql_validator" not in imports
    assert "class ValidateSqlTool" not in core_source
    assert "class ExecuteSqlTool" not in core_source


def test_public_tools_do_not_read_chatbi_state_or_build_services_at_runtime():
    tool_paths = (
        "apps/tool/tools/datasource.py",
        "apps/tool/tools/semantic.py",
        "apps/tool/tools/knowledge.py",
    )
    for relative_path in tool_paths:
        source = (BACKEND_DIR__query / relative_path).read_text(encoding="utf-8")
        assert "ctx.state" not in source
        assert "ctx.session" not in source
        assert "build_" not in source
        assert "result_artifact" not in source
        assert "EventPublisher" not in source
        assert "AgentTracer" not in source

    core_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    interaction_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/interaction.py"
    ).read_text(encoding="utf-8")
    for class_name in (
        "GetDatasetSchemaTool",
        "ValidateSqlTool",
        "ExecuteSqlTool",
        "SearchTerminologyTool",
        "GetSqlExamplesTool",
    ):
        assert f"class {class_name}" not in core_source
        assert f"class {class_name}" not in interaction_source


def test_graph_sql_adapter_does_not_maintain_second_execution_chain():
    path = BACKEND_DIR__query / "apps/chatbi/orchestration/graph/capabilities/adapters/sql.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports__query("apps/chatbi/orchestration/graph/capabilities/adapters/sql.py")

    assert "apps.chatbi.services.execution" in imports
    assert "self._execute_tool" not in source
    assert "self._validate_tool" not in source
    assert "self._permission_adapter.apply" not in source


def test_datasource_query_service_has_no_session_or_repository_dependency():
    imports = _imports__query("apps/datasource/services/query_service.py")

    assert "sqlmodel" not in imports
    assert not any(".repository" in module for module in imports)


def test_agent_and_graph_share_chatbi_semantic_query_service():
    agent_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/agent/tools/core.py"
    ).read_text(encoding="utf-8")
    graph_source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/graph/capabilities/adapters/sql.py"
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
    schema_source = (
        BACKEND_DIR__query / "apps/tool/tools/datasource.py"
    ).read_text(encoding="utf-8")

    assert "apps.capabilities.semantic.retrieval" not in imports
    assert "apps.datasource" in imports
    assert not any(module.startswith("apps.datasource.models") for module in imports)
    assert "semantic_retrieval_service.retrieve_for_agent" in source
    assert "self._schema_reader.get" in schema_source
    assert "class GetDatasetSchemaTool" not in source


def test_graph_semantic_retrieval_uses_chatbi_service():
    imports = _imports__query("apps/chatbi/orchestration/graph/capabilities/adapters/knowledge.py")
    source = (
        BACKEND_DIR__query / "apps/chatbi/orchestration/graph/capabilities/adapters/knowledge.py"
    ).read_text(encoding="utf-8")

    assert "apps.chatbi.services.planning" in imports
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


def test_graph_layers_do_not_reimplement_dimension_time_rule():
    question_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")
    graph_contracts_source = (
        BACKEND_DIR__question_understanding
        / f"{UNDERSTANDING__question_understanding}/graph_contracts.py"
    ).read_text(encoding="utf-8")

    assert "def _is_time_expression" not in question_source
    assert "_dimension_time_value_violations" not in graph_contracts_source


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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "class LangChainQuestionModelClient" in adapter_source
    assert "LLMFactory" in adapter_source
    assert "DefaultQuestionUnderstandingModelClient" not in agent_source
    assert "DefaultQuestionClassificationModelClient" not in graph_source
    assert "build_question_model_service" in composition_source


def test_question_understanding_dtos_are_owned_by_chatbi():
    dto_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/models/dto/question_understanding.py"
    ).read_text(encoding="utf-8")
    graph_source = (BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/schemas/v1.py").read_text(
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
        assert f"class {class_name}" in dto_source

    assert "QuestionClassificationOutputBase" in graph_source
    assert "QuestionRewriteProjectionOutput" in graph_source
    assert "NaturalLanguageIntentOutputBase" in graph_source


def test_question_understanding_prompt_rules_are_owned_by_chatbi():
    prompt_import = "apps.chatbi.services.understanding.prompts"
    agent_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/understanding_service.py")
    graph_prompt_imports = set().union(
        *(
            _imports__question_understanding(
                f"apps/chatbi/orchestration/graph/capabilities/adapters/{filename}"
            )
            for filename in (
                "question_input.py",
                "question_intent.py",
                "question_dimension.py",
            )
        )
    )
    prompt_imports = _imports__question_understanding(f"{UNDERSTANDING__question_understanding}/prompts.py")

    assert prompt_import in agent_imports
    assert prompt_import in graph_prompt_imports
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


def test_old_question_understanding_path_has_been_removed():
    compatibility_path = "apps/capabilities/question_understanding.py"
    agent_loop_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/loop.py"
    ).read_text(encoding="utf-8")

    assert not (BACKEND_DIR__question_understanding / compatibility_path).exists()
    assert "apps.capabilities.question_understanding" not in agent_loop_source


def test_agent_input_preparation_owns_understanding_and_preflight_clarification():
    loop_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/loop.py"
    ).read_text(encoding="utf-8")
    preparation_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/preparation.py"
    ).read_text(encoding="utf-8")

    assert "self.input_preparer.prepare_initial" in loop_source
    assert "self.input_preparer.prepare_resume" in loop_source
    assert "self.understanding_service.understand" not in loop_source
    assert "apply_question_understanding_clarification" not in loop_source
    assert "def _preflight_clarification" in preparation_source
    assert "build_system_prompt" in preparation_source


def test_agent_runtime_dependencies_are_owned_by_composition():
    loop_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/loop.py"
    ).read_text(encoding="utf-8")
    composition_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/composition.py"
    ).read_text(encoding="utf-8")
    service_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/service.py"
    ).read_text(encoding="utf-8")
    state_source = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/agent/state.py"
    ).read_text(encoding="utf-8")

    assert "def build_agent_loop" in composition_source
    assert "def build_agent_tool_registry" in composition_source
    assert "build_agent_loop(stream_session" in service_source
    assert "apps.chatbi.composition" not in loop_source
    assert "build_default_tools" not in loop_source
    assert "LLMFactory" not in loop_source
    assert "AgentRuntimeStateFactory(" in composition_source
    assert "BudgetGuard(" in state_source
    assert "AgentToolContext(" in state_source
    assert "BudgetGuard(" not in loop_source
    assert "AgentToolContext(" not in loop_source


def test_graph_intent_projection_rules_are_owned_by_chatbi():
    service_path = f"{UNDERSTANDING__question_understanding}/intent_projection.py"
    service_imports = _imports__question_understanding(service_path)
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "graph_contracts.validate_intent" in graph_source
    assert "apps.chatbi.orchestration.graph.capabilities.adapters.intent_validation" not in graph_source


def test_old_graph_understanding_compatibility_paths_are_removed():
    adapter_dir = (
        BACKEND_DIR__question_understanding
        / "apps/chatbi/orchestration/graph/capabilities/adapters"
    )
    contracts_source = (
        BACKEND_DIR__question_understanding
        / f"{UNDERSTANDING__question_understanding}/graph_contracts.py"
    ).read_text(encoding="utf-8")

    assert not (adapter_dir / "intent_validation.py").exists()
    assert not (adapter_dir / "time_slots.py").exists()
    assert "class QuestionIntentValidationService" not in contracts_source


def test_graph_keeps_intent_model_retry_orchestration():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "def _recognize_subtask" in graph_source
    assert "for retry_count in range" in graph_source
    assert "def _run_intent_subtasks" in graph_source


def test_question_input_projection_rules_are_owned_by_chatbi():
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
    ).read_text(encoding="utf-8")

    assert "CLASSIFICATION_MODEL_CALL_FAILED" in graph_source
    assert "CLASSIFICATION_MODEL_OUTPUT_INVALID" in graph_source
    assert "except Exception:" in graph_source
    assert "graph_contracts.fallback_rewrite" in graph_source


def test_question_intent_fallback_rules_are_owned_by_chatbi():
    service_path = f"{UNDERSTANDING__question_understanding}/intent_fallback.py"
    service_imports = _imports__question_understanding(service_path)
    graph_source = (
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
        BACKEND_DIR__question_understanding / "apps/chatbi/orchestration/graph/capabilities/adapters/question.py"
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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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
    imports = _imports__record(_tree__record("apps/conversation/services/chat_record.py"))

    assert "sqlmodel" not in imports
    assert not any(module.startswith("apps.agent.") for module in imports)
    assert not any(module.startswith("sqlbot_platform.workflow_engine.") for module in imports)
    assert not any(module.startswith("apps.chat.") for module in imports)


def test_chat_history_dto_has_no_legacy_chat_or_framework_dependency():
    imports = _imports__record(_tree__record("apps/conversation/models/dto/chat_history.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_legacy_query_dto_has_no_transport_or_model_framework_dependency():
    imports = _imports__record(_tree__record("apps/chatbi/models/dto/legacy_query.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert not any(module.startswith("apps.agent") for module in imports)
    assert not any(module.startswith("apps.workflow") for module in imports)
    assert "fastapi" not in imports
    assert "langchain" not in imports
    assert "sqlmodel" not in imports


def test_agent_record_terminal_projection_uses_chatbi_service():
    tree = _tree__record("apps/chatbi/orchestration/agent/lifecycle.py")
    lifecycle_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentLifecycle"
    )
    finish_source = ast.unparse(
        next(
            node
            for node in lifecycle_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "finish"
        )
    )
    fail_source = ast.unparse(
        next(
            node
            for node in lifecycle_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "fail"
        )
    )
    loop_source = (BACKEND_DIR__record / "apps/chatbi/orchestration/agent/loop.py").read_text()

    assert "self._record_service.transition" in finish_source
    assert "self._record_service.transition" in fail_source
    assert ".status =" not in finish_source
    assert ".finish =" not in finish_source
    assert "ChatRecordStatus" not in loop_source


def test_workflow_projector_and_api_service_have_no_business_imports():
    service_imports = _imports__record(
        _tree__record("platform/workflow_engine/api/service.py")
    )
    extension_imports = _imports__record(
        _tree__record("apps/chatbi/orchestration/graph/api_extension.py")
    )

    assert not (
        BACKEND_DIR__record / "platform/workflow_engine/api/chat_history.py"
    ).exists()
    assert not any(module.startswith("apps.") for module in service_imports)
    assert "apps.chatbi.orchestration.graph.runtime" in extension_imports
    assert "apps.semantic.composition" in extension_imports
    assert "GraphChatRecordProjector" in (
        BACKEND_DIR__record / "apps/chatbi/orchestration/graph/api_extension.py"
    ).read_text(encoding="utf-8")


def test_graph_chat_binding_rule_is_owned_by_chatbi_extension():
    tree = _tree__record("apps/chatbi/orchestration/graph/api_extension.py")
    service_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ChatBIWorkflowApiExtension"
    )
    method = next(
        node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_chat_query"
    )
    service_source = ast.unparse(method)

    assert "resolve_execution_binding" in service_source
    assert "chat.dataset_id" in service_source
    assert "CHAT_DATASET_MISMATCH" not in service_source


def test_chat_record_service_owns_final_result_size_policy():
    service_source = (
        BACKEND_DIR__record / "apps/conversation/services/chat_record.py"
    ).read_text(encoding="utf-8")
    agent_lifecycle_source = (
        BACKEND_DIR__record / "apps/chatbi/orchestration/agent/lifecycle.py"
    ).read_text(encoding="utf-8")

    assert "ChatRecordResultLimits" in service_source
    assert "CHAT_RECORD_DATA_TOO_LARGE" in service_source
    assert 'record_payload["artifact_ref"]' in agent_lifecycle_source


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
    assert not any(module.startswith("sqlbot_platform.workflow_engine") for module in imports)


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
    imports = _imports__mcp_schema(_tree__mcp_schema("interfaces/mcp/schemas.py"))

    assert not any(module.startswith("apps.chat") for module in imports)
    assert "fastapi" not in imports
    assert "sqlmodel" not in imports


def test_mcp_routes_use_owned_request_schemas():
    imports = _imports__mcp_schema(_tree__mcp_schema("interfaces/mcp/router.py"))

    assert "interfaces.mcp.schemas" in imports
    assert "apps.chat.models.chat_model" not in imports


def test_legacy_chat_package_has_been_removed():
    assert not (BACKEND_DIR__mcp_schema / "apps/chat/__init__.py").exists()


# ======================================================================
# R6-c：删除 LLMService 后的辅助生成边界
# ======================================================================

BACKEND_DIR__r6c = Path(__file__).resolve().parents[2]


def _tree__r6c(relative_path: str) -> ast.Module:
    path = BACKEND_DIR__r6c / relative_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports__r6c(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class_method_source__r6c(tree: ast.Module, class_name: str, name: str) -> str:
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


def test_r6c_legacy_llm_files_are_removed():
    api_dir = BACKEND_DIR__r6c / "apps/chatbi/api"
    for name in (
        "legacy_chat_flow.py",
        "legacy_sse.py",
        "legacy_external_datasource.py",
        "legacy_read.py",
    ):
        assert not (api_dir / name).exists()


def test_r6c_queries_do_not_import_legacy_or_llmservice():
    source = (BACKEND_DIR__r6c / "apps/chatbi/api/queries.py").read_text(encoding="utf-8")
    imports = _imports__r6c(_tree__r6c("apps/chatbi/api/queries.py"))

    assert "apps.chatbi.api.legacy_chat_flow" not in imports
    assert "apps.chatbi.api.legacy_sse" not in imports
    assert "apps.chatbi.api.legacy_external_datasource" not in imports
    assert "LLMService" not in source
    assert "build_auxiliary_generation_service" in source
    assert "from apps.chatbi.api.sse import encode_sse_event" in source


def test_r6c_auxiliary_generation_uses_generation_services():
    tree = _tree__r6c("apps/chatbi/services/generation/auxiliary_generation.py")
    source = tree.body and ast.unparse(tree)
    imports = _imports__r6c(tree)

    assert "apps.chatbi.services.generation.analysis_prediction" in imports
    assert "apps.chatbi.services.generation.recommended_questions" in imports
    assert "apps.chatbi.api.sse" in imports
    assert "apps.chatbi.adapters.assistant_schema" in imports
    assert "AnalysisPredictionService" in source
    assert "RecommendedQuestionService" in source
    assert "encode_sse_event" in source


def test_r6c_auxiliary_generation_keeps_sse_event_contract():
    source = (
        BACKEND_DIR__r6c / "apps/chatbi/services/generation/auxiliary_generation.py"
    ).read_text(encoding="utf-8")
    for event_type in (
        "recommended_question_result",
        "recommended_question",
        "analysis-result",
        "analysis_finish",
        "predict-result",
        "predict-success",
        "predict-failed",
        "predict_finish",
    ):
        assert event_type in source


def test_r6c_auxiliary_generation_writes_via_conversation_services():
    tree = _tree__r6c("apps/chatbi/services/generation/auxiliary_generation.py")
    finish_source = _class_method_source__r6c(
        tree, "AuxiliaryGenerationService", "_finish"
    )
    error_source = _class_method_source__r6c(
        tree, "AuxiliaryGenerationService", "_save_error"
    )
    predict_source = _class_method_source__r6c(
        tree, "AuxiliaryGenerationService", "_save_predict_data"
    )

    assert "transition_by_id" in finish_source
    assert "transition_by_id" in error_source
    assert "project_auxiliary_by_id" in predict_source
    assert "update(ChatRecord)" not in finish_source + error_source + predict_source


def test_r6c_sse_module_only_depends_on_serialization_library():
    imports = _imports__r6c(_tree__r6c("apps/chatbi/api/sse.py"))
    assert imports == {"collections.abc", "typing", "orjson"}


def test_r6c_queries_create_auxiliary_via_chat_record_service():
    source = (BACKEND_DIR__r6c / "apps/chatbi/api/queries.py").read_text(encoding="utf-8")
    assert "create_auxiliary" in source
    assert "ChatRecordAuxiliaryType" in source
    assert ".analysis_record_id =" not in source
    assert ".predict_record_id =" not in source


def test_r6c_langchain_adapter_uses_shared_model_stream_parser():
    adapter_tree = _tree__r6c("apps/chatbi/adapters/langchain.py")
    adapter_imports = _imports__r6c(adapter_tree)
    assert "apps.ai_model.streaming" in adapter_imports
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "process_stream"
        for node in adapter_tree.body
    )


def test_r6c_conversations_use_public_axis_schema():
    api_imports = _imports__r6c(_tree__r6c("apps/chatbi/api/conversations.py"))
    assert "common.utils.data_format_schema" in api_imports


def test_r6c_datasource_query_executor_owns_direct_connection_execution():
    adapter_source = (
        BACKEND_DIR__r6c / "apps/datasource/services/query_executor.py"
    ).read_text(encoding="utf-8")
    history_source = (
        BACKEND_DIR__r6c / "apps/chatbi/services/conversation/history_reader.py"
    ).read_text(encoding="utf-8")
    dashboard_source = (
        BACKEND_DIR__r6c / "apps/dashboard/composition.py"
    ).read_text(encoding="utf-8")
    assert ".execute_query(" in adapter_source
    assert ".execute_query(" not in history_source
    assert ".execute_query(" not in dashboard_source


# ======================================================================
# R6-d：清理组合入口
# ======================================================================


def test_r6d_legacy_composition_is_removed():
    api_dir = BACKEND_DIR__r6c / "apps/chatbi/api"
    assert not any(path.name.startswith("legacy_") for path in api_dir.glob("*.py"))


def test_r6d_router_configures_agent_cleanup_via_composition():
    source = (BACKEND_DIR__r6c / "apps/chatbi/api/router.py").read_text(encoding="utf-8")
    imports = _imports__r6c(_tree__r6c("apps/chatbi/api/router.py"))

    assert "apps.chatbi.composition" in imports
    assert "configure_agent_cleanup" in source
    assert "legacy_composition" not in source
    assert "compose_chatbi_router" in source


def test_r6d_chat_application_service_coordinates_create_and_delete():
    tree = _tree__r6c("apps/chatbi/services/conversation/chat_application.py")
    source = ast.unparse(tree)
    imports = _imports__r6c(tree)

    assert "ChatApplicationService" in source
    assert "create_from_request" in source
    assert "validate_assistant_dataset_binding" in imports or "validate_assistant_dataset_binding" in source
    assert "Legacy" not in source


def test_r6d_chatbi_public_surface_does_not_export_conversation_internals():
    init_source = (BACKEND_DIR__r6c / "apps/chatbi/__init__.py").read_text(encoding="utf-8")
    assert "Chat" not in init_source or "ChatBIError" in init_source
    assert "conversation_repository" not in init_source
    assert "ChatRecordService" not in init_source
