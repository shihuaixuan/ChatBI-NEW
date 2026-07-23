import json
from typing import Any

from fastapi import Request
from starlette.datastructures import UploadFile

from apps.platform_config.models import SysArgModel
from apps.platform_config.repository.sqlmodel import (
    SQLModelPlatformParameterRepository,
)
from common.core.deps import SessionDep
from common.utils.file_utils import SQLBotFileUtils


async def get_group_args(
    session: SessionDep,
    flag: str | None = None,
) -> list[SysArgModel]:
    return SQLModelPlatformParameterRepository(session).list_models(flag)


async def save_group_args(
    session: SessionDep,
    sys_args: list[SysArgModel],
    file_mapping: dict[str, Any] | None = None,
) -> None:
    SQLModelPlatformParameterRepository(session).save_models(
        sys_args,
        file_mapping,
    )


async def get_parameter_args(session: SessionDep) -> list[SysArgModel]:
    group_args = await get_group_args(session=session)
    return [
        item
        for item in group_args
        if not item.pkey.startswith("appearance.")
    ]


async def get_groups(session: SessionDep, flag: str) -> list[SysArgModel]:
    return await get_group_args(session=session, flag=flag)


async def save_parameter_args(session: SessionDep, request: Request) -> None:
    allow_file_mapping: dict[str, dict[str, Any]] = {}
    form_data = await request.form()
    files = form_data.getlist("files")
    json_text = form_data.get("data")
    if not isinstance(json_text, str):
        raise ValueError("参数 data 必须是 JSON 字符串")
    payload = json.loads(json_text)
    if not isinstance(payload, list):
        raise ValueError("参数 data 必须是 JSON 数组")
    sys_args = [
        SysArgModel(**{**item, "pkey": f"{item['pkey']}"})
        for item in payload
        if isinstance(item, dict) and "pkey" in item
    ]
    if not sys_args:
        return

    file_mapping: dict[str, Any] | None = None
    if files:
        file_mapping = {}
        for file in files:
            if not isinstance(file, UploadFile):
                raise ValueError("files 参数必须是上传文件")
            origin_file_name = file.filename or ""
            file_name, flag_name = SQLBotFileUtils.split_filename_and_flag(
                origin_file_name
            )
            file.filename = file_name
            allow_limit = allow_file_mapping.get(flag_name)
            if allow_limit is None:
                raise ValueError(
                    f"The file [{file_name}] is not allowed to be uploaded!"
                )
            SQLBotFileUtils.check_file(
                file=file,
                file_types=allow_limit.get("types"),
                limit_file_size=allow_limit.get("size"),
            )
            file_mapping[flag_name] = await SQLBotFileUtils.upload(file)

    await save_group_args(
        session=session,
        sys_args=sys_args,
        file_mapping=file_mapping,
    )
