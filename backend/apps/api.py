from fastapi import APIRouter
from sqlbot_xpack.authentication import api as xpack_authentication
from sqlbot_xpack.config import api as xpack_config
from sqlbot_xpack.license import api as xpack_license

from apps.access_control.api import access_variable
from apps.access_control.api import api_key as access_api_key
from apps.access_control.api import login as access_login
from apps.access_control.api import user as access_user
from apps.access_control.api import workspace as access_workspace
from apps.ai_model.api import model_config as ai_model
from apps.assistant.api import assistants as assistant
from apps.chatbi.api.router import compose_chatbi_router
from apps.chatbi.orchestration.graph.api_extension import (
    build_chatbi_workflow_api_extension,
)
from apps.dashboard.api import dashboard_api
from apps.datasource.api import datasource, table_relation
from apps.knowledge.api import recommended_problem, sql_example
from apps.platform_config.api import router as platform_config
from apps.semantic.api import legacy_terms
from apps.semantic.api.router import router as semantic_router
from apps.system.api import user
from interfaces.http import file_download
from interfaces.mcp import router as mcp_router
from sqlbot_platform.workflow_engine.api import router as graph_workflow
from sqlbot_platform.workflow_engine.api.extension import (
    register_workflow_api_extension,
)

#from audit.api import audit_api


api_router = APIRouter()
register_workflow_api_extension(build_chatbi_workflow_api_extension)
chatbi_router = compose_chatbi_router(
    graph_router=graph_workflow.router,
)
api_router.include_router(access_login.router)
api_router.include_router(access_user.router)
api_router.include_router(user.router)
api_router.include_router(access_workspace.router)
api_router.include_router(assistant.router)
api_router.include_router(ai_model.router)
api_router.include_router(file_download.router)
api_router.include_router(sql_example.router)
api_router.include_router(datasource.router)
api_router.include_router(chatbi_router)
api_router.include_router(dashboard_api.router)
api_router.include_router(mcp_router)
api_router.include_router(table_relation.router)
api_router.include_router(platform_config.router)
api_router.include_router(access_api_key.router)

api_router.include_router(recommended_problem.router)

api_router.include_router(access_variable.router)
api_router.include_router(legacy_terms.router)
api_router.include_router(semantic_router)

# 前端登录加密与授权初始化依赖 xpack 提供的 key/license/status 接口。
api_router.include_router(xpack_config.router)
api_router.include_router(xpack_license.router)
api_router.include_router(xpack_authentication.router)

#api_router.include_router(audit_api.router)
