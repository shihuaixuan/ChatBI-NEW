from fastapi import APIRouter

from apps.access_control.api import access_variable
from apps.access_control.api import api_key as access_api_key
from apps.access_control.api import data_permission as access_data_permission
from apps.access_control.api import login as access_login
from apps.access_control.api import user as access_user
from apps.access_control.api import workspace as access_workspace
from apps.ai_model.api import model_config as ai_model
from apps.assistant.api import assistants as assistant
from apps.assistant.api import page_embedded
from apps.chatbi.api.router import compose_chatbi_router
from apps.dashboard.api import dashboard_api
from apps.datasource.api import datasource, table_relation
from apps.knowledge.api import recommended_problem, sql_example
from apps.memory.api import router as memory_router
from apps.platform_config.api import appearance
from apps.platform_config.api import router as platform_config
from apps.semantic.api import legacy_terms
from apps.semantic.api.router import router as semantic_router
from apps.system.api import user
from interfaces.http import file_download
from interfaces.mcp import router as mcp_router

api_router = APIRouter()
chatbi_router = compose_chatbi_router()
api_router.include_router(access_login.router)
api_router.include_router(access_user.router)
api_router.include_router(user.router)
api_router.include_router(access_workspace.router)
api_router.include_router(assistant.router)
api_router.include_router(page_embedded.router)
api_router.include_router(ai_model.router)
api_router.include_router(file_download.router)
api_router.include_router(sql_example.router)
api_router.include_router(datasource.router)
api_router.include_router(chatbi_router)
api_router.include_router(dashboard_api.router)
api_router.include_router(mcp_router)
api_router.include_router(table_relation.router)
api_router.include_router(platform_config.router)
api_router.include_router(appearance.router)
api_router.include_router(access_api_key.router)
api_router.include_router(access_data_permission.router)

api_router.include_router(recommended_problem.router)
api_router.include_router(memory_router)

api_router.include_router(access_variable.router)
api_router.include_router(legacy_terms.router)
api_router.include_router(semantic_router)
