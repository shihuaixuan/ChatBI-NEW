from fastapi import APIRouter
from sqlbot_xpack.authentication import api as xpack_authentication
from sqlbot_xpack.config import api as xpack_config
from sqlbot_xpack.license import api as xpack_license

from apps.access_control.api import access_variable
from apps.access_control.api import api_key as access_api_key
from apps.access_control.api import login as access_login
from apps.access_control.api import user as access_user
from apps.access_control.api import workspace as access_workspace
from apps.agent import api as agent
from apps.agent.deletion import AgentExecutionDeletionService
from apps.ai_model.api import model_config as ai_model
from apps.assistant.api import assistants as assistant
from apps.chatbi.api import router as chat
from apps.chatbi.api.legacy_composition import configure_legacy_agent_cleanup
from apps.dashboard.api import dashboard_api
from apps.datasource.api import datasource, table_relation
from apps.knowledge.api import recommended_problem, sql_example
from apps.mcp import mcp
from apps.semantic.api import legacy_terms
from apps.semantic.api.router import router as semantic_router
from apps.settings.api import base
from apps.system.api import parameter, user
from apps.workflow_engine.api import router as graph_workflow

#from audit.api import audit_api


api_router = APIRouter()
configure_legacy_agent_cleanup(AgentExecutionDeletionService)
api_router.include_router(access_login.router)
api_router.include_router(access_user.router)
api_router.include_router(user.router)
api_router.include_router(access_workspace.router)
api_router.include_router(assistant.router)
api_router.include_router(ai_model.router)
api_router.include_router(base.router)
api_router.include_router(sql_example.router)
api_router.include_router(datasource.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
api_router.include_router(dashboard_api.router)
api_router.include_router(mcp.router)
api_router.include_router(table_relation.router)
api_router.include_router(parameter.router)
api_router.include_router(access_api_key.router)

api_router.include_router(recommended_problem.router)

api_router.include_router(access_variable.router)
api_router.include_router(legacy_terms.router)
api_router.include_router(semantic_router)
api_router.include_router(graph_workflow.router)

# 前端登录加密与授权初始化依赖 xpack 提供的 key/license/status 接口。
api_router.include_router(xpack_config.router)
api_router.include_router(xpack_license.router)
api_router.include_router(xpack_authentication.router)

#api_router.include_router(audit_api.router)
