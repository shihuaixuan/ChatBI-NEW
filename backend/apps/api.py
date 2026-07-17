from fastapi import APIRouter
from sqlbot_xpack.authentication import api as xpack_authentication
from sqlbot_xpack.config import api as xpack_config
from sqlbot_xpack.license import api as xpack_license

from apps.chat.api import chat
from apps.agent import api as agent
from apps.dashboard.api import dashboard_api
from apps.data_training.api import data_training
from apps.datasource.api import datasource, recommended_problem, table_relation
from apps.semantic import api as semantic
from apps.mcp import mcp
from apps.settings.api import base
from apps.system.api import (
    aimodel,
    apikey,
    assistant,
    login,
    parameter,
    user,
    variable_api,
    workspace,
)
from apps.terminology.api import terminology
from apps.workflow_engine.api import router as graph_workflow

#from audit.api import audit_api


api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(user.router)
api_router.include_router(workspace.router)
api_router.include_router(assistant.router)
api_router.include_router(aimodel.router)
api_router.include_router(base.router)
api_router.include_router(terminology.router)
api_router.include_router(data_training.router)
api_router.include_router(datasource.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
api_router.include_router(dashboard_api.router)
api_router.include_router(mcp.router)
api_router.include_router(table_relation.router)
api_router.include_router(parameter.router)
api_router.include_router(apikey.router)

api_router.include_router(recommended_problem.router)

api_router.include_router(variable_api.router)
api_router.include_router(semantic.router)
api_router.include_router(graph_workflow.router)

# 前端登录加密与授权初始化依赖 xpack 提供的 key/license/status 接口。
api_router.include_router(xpack_config.router)
api_router.include_router(xpack_license.router)
api_router.include_router(xpack_authentication.router)

#api_router.include_router(audit_api.router)
