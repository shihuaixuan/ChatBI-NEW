from sqlalchemy import String, func, literal_column, select, union_all
from sqlalchemy.sql import Select

from apps.access_control.models import (
    ApiKeyModel,
    DataPermissionModel,
    DataRuleModel,
    UserModel,
    WorkspaceModel,
)
from apps.ai_model.models import AiModelDetail
from apps.assistant.audit import build_assistant_audit_resource_query
from apps.conversation.models import Chat
from apps.dashboard.models.orm import CoreDashboard
from apps.datasource.models.orm import CoreDatasource
from apps.knowledge.models.orm import SQLExampleModel
from apps.semantic.models.orm import SemanticTerm


def build_resource_union_query() -> Select:
    """
    构建资源名称的union查询
    返回包含id, name, module的查询
    """
    # 创建各个子查询，每个查询都包含module字段

    # ai_model 表查询
    ai_model_query = select(
        func.cast(AiModelDetail.id, String).label("id"),
        AiModelDetail.name.label("name"),
        literal_column("'ai_model'").label("module")
    ).select_from(AiModelDetail)

    # chat 表查询（使用brief作为name）
    chat_query = select(
        func.cast(Chat.id, String).label("id"),
        Chat.brief.label("name"),
        literal_column("'chat'").label("module")
    ).select_from(Chat)

    # dashboard 表查询
    dashboard_query = select(
        func.cast(CoreDashboard.id, String).label("id"),
        CoreDashboard.name.label("name"),
        literal_column("'dashboard'").label("module")
    ).select_from(CoreDashboard)

    # datasource 表查询
    datasource_query = select(
        func.cast(CoreDatasource.id, String).label("id"),
        CoreDatasource.name.label("name"),
        literal_column("'datasource'").label("module")
    ).select_from(CoreDatasource)

    # data_training 表查询（使用question作为name）
    data_training_query = select(
        func.cast(SQLExampleModel.id, String).label("id"),
        SQLExampleModel.question.label("name"),
        literal_column("'data_training'").label("module")
    ).select_from(SQLExampleModel)

    # ds_permission 表查询
    ds_permission_query = select(
        func.cast(DataPermissionModel.id, String).label("id"),
        DataPermissionModel.name.label("name"),
        literal_column("'permission'").label("module")
    ).select_from(DataPermissionModel)

    # ds_rules 表查询
    ds_rules_query = select(
        func.cast(DataRuleModel.id, String).label("id"),
        DataRuleModel.name.label("name"),
        literal_column("'rules'").label("module")
    ).select_from(DataRuleModel)

    # sys_user 表查询
    user_query = select(
        func.cast(UserModel.id, String).label("id"),
        UserModel.name.label("name"),
        literal_column("'user'").label("module")
    ).select_from(UserModel)

    # sys_user 表查询
    member_query = select(
        func.cast(UserModel.id, String).label("id"),
        UserModel.name.label("name"),
        literal_column("'member'").label("module")
    ).select_from(UserModel)

    # sys_workspace 表查询
    sys_workspace_query = select(
        func.cast(WorkspaceModel.id, String).label("id"),
        WorkspaceModel.name.label("name"),
        literal_column("'workspace'").label("module")
    ).select_from(WorkspaceModel)

    # Semantic 术语表查询
    terminology_query = select(
        func.cast(SemanticTerm.id, String).label("id"),
        SemanticTerm.name.label("name"),
        literal_column("'terminology'").label("module")
    ).select_from(SemanticTerm)

    # sys_assistant 表查询
    sys_assistant_query = build_assistant_audit_resource_query()

    # sys_apikey 表查询
    sys_apikey_query = select(
        func.cast(ApiKeyModel.id, String).label("id"),
        ApiKeyModel.access_key.label("name"),
        literal_column("'api_key'").label("module")
    ).select_from(ApiKeyModel)

    # 使用 union_all() 方法连接所有查询
    union_query = union_all(
        ai_model_query,
        chat_query,
        dashboard_query,
        datasource_query,
        data_training_query,
        ds_permission_query,
        ds_rules_query,
        user_query,
        member_query,
        sys_workspace_query,
        terminology_query,
        sys_assistant_query,
        sys_apikey_query
    )

    # 返回查询，包含所有字段
    return select(union_query.c.id, union_query.c.name, union_query.c.module)
