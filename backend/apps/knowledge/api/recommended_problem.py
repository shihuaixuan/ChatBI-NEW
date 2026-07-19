"""推荐问题兼容 HTTP API。"""

# mypy: disable-error-code="untyped-decorator"

from fastapi import APIRouter, HTTPException

from apps.knowledge.composition import build_recommended_problem_service
from apps.knowledge.errors import (
    RecommendedProblemDatasourceNotFoundError,
    RecommendedProblemRequestError,
)
from apps.knowledge.models.dto import (
    RecommendedProblemBase,
    RecommendedProblemItem,
    RecommendedProblemResponse,
)
from apps.swagger.i18n import PLACEHOLDER_PREFIX
from common.audit.models.log_model import OperationModules, OperationType
from common.audit.schemas.logger_decorator import LogConfig, system_log
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["recommended problem"], prefix="/recommended_problem")


@router.get(
    "/get_datasource_recommended/{ds_id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}rp_get",
)
async def get_datasource_recommended(
    session: SessionDep,
    ds_id: int,
) -> list[RecommendedProblemItem]:
    return build_recommended_problem_service(session).list_by_datasource(ds_id)


@router.get(
    "/get_datasource_recommended_base/{ds_id}",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}rp_base",
)
async def get_datasource_recommended_base(
    session: SessionDep,
    ds_id: int,
) -> RecommendedProblemResponse:
    return build_recommended_problem_service(session).get_base(ds_id)


@router.post(
    "/save_recommended_problem",
    response_model=None,
    summary=f"{PLACEHOLDER_PREFIX}rp_save",
)
@system_log(
    LogConfig(
        operation_type=OperationType.UPDATE,
        module=OperationModules.DATASOURCE,
        resource_id_expr="data_info.datasource_id",
    )
)
async def save_recommended_problem(
    session: SessionDep,
    user: CurrentUser,
    data_info: RecommendedProblemBase,
) -> None:
    try:
        return build_recommended_problem_service(session).replace(
            data_info,
            actor_id=user.id,
        )
    except RecommendedProblemDatasourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecommendedProblemRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
