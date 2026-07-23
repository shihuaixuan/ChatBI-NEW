"""系统外观配置接口。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import col, select
from starlette.datastructures import UploadFile

from apps.access_control.permission import SqlbotPermission, require_permissions
from apps.platform_config.models import SysArgModel
from common.core.deps import SessionDep
from common.utils.file_utils import SQLBotFileUtils

router = APIRouter(
    tags=["system/appearance"],
    prefix="/system/appearance",
    include_in_schema=False,
)

FILE_LIMITS = {
    "web": 200 * 1024,
    "login": 200 * 1024,
    "bg": 5 * 1024 * 1024,
    "navigate": 5 * 1024 * 1024,
    "mobileLogin": 5 * 1024 * 1024,
    "mobileLoginBg": 5 * 1024 * 1024,
}


@router.get("/ui")
async def get_ui(session: SessionDep) -> list[SysArgModel]:
    items = session.exec(
        select(SysArgModel)
        .where(col(SysArgModel.pkey).startswith("appearance."))
        .order_by(col(SysArgModel.sort_no), col(SysArgModel.id))
    ).all()
    return [
        SysArgModel(
            id=item.id,
            pkey=item.pkey.removeprefix("appearance."),
            pval=item.pval,
            ptype=item.ptype,
            sort_no=item.sort_no,
        )
        for item in items
    ]


@router.post("")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def save_ui(session: SessionDep, request: Request) -> None:
    form_data = await request.form()
    json_text = form_data.get("data")
    if not isinstance(json_text, str):
        raise ValueError("参数 data 必须是 JSON 字符串")
    payload = json.loads(json_text)
    if not isinstance(payload, list):
        raise ValueError("参数 data 必须是 JSON 数组")

    requested: dict[str, SysArgModel] = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("pkey"), str):
            raise ValueError("外观参数格式不合法")
        pkey = item["pkey"]
        requested[pkey] = SysArgModel(
            pkey=f"appearance.{pkey}",
            pval=None if item.get("pval") is None else str(item.get("pval")),
            ptype=str(item.get("ptype") or "str"),
            sort_no=int(item.get("sort_no") or item.get("sort") or 1),
        )

    uploaded: dict[str, str] = {}
    uploaded_for_cleanup: list[str] = []
    try:
        for raw_file in form_data.getlist("files"):
            if not isinstance(raw_file, UploadFile):
                raise ValueError("files 参数必须是上传文件")
            file_name, flag_name = SQLBotFileUtils.split_filename_and_flag(
                raw_file.filename
            )
            limit = FILE_LIMITS.get(flag_name)
            if limit is None or flag_name not in requested:
                raise ValueError(f"不允许上传用途为 {flag_name} 的文件")
            raw_file.filename = file_name
            SQLBotFileUtils.check_file(
                raw_file,
                [".jpg", ".jpeg", ".png", ".svg"],
                limit,
            )
            file_id = await SQLBotFileUtils.upload(raw_file)
            uploaded[flag_name] = file_id
            uploaded_for_cleanup.append(file_id)

        existing = {
            item.pkey: item
            for item in session.exec(
                select(SysArgModel).where(
                    col(SysArgModel.pkey).in_(
                        [item.pkey for item in requested.values()]
                    )
                )
            ).all()
        }
        obsolete_file_ids: list[str] = []
        for flag_name, item in requested.items():
            current = existing.get(item.pkey)
            if item.ptype == "file":
                new_file_id = uploaded.get(flag_name)
                if new_file_id:
                    item.pval = new_file_id
                elif item.pval:
                    # 未上传新文件时保留已有文件，前端临时 uid 不写入数据库。
                    item.pval = current.pval if current else None
                if (
                    current
                    and current.pval
                    and current.pval != item.pval
                ):
                    obsolete_file_ids.append(current.pval)
            if current is None:
                session.add(item)
            else:
                current.pval = item.pval
                current.ptype = item.ptype
                current.sort_no = item.sort_no
                session.add(current)
        session.commit()
    except Exception:
        for file_id in uploaded_for_cleanup:
            SQLBotFileUtils.delete_file(file_id)
        raise

    for file_id in obsolete_file_ids:
        SQLBotFileUtils.delete_file(file_id)


@router.get("/picture/{file_id}")
async def picture(file_id: str) -> StreamingResponse:
    file_path = Path(SQLBotFileUtils.get_file_path(file_id))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }.get(file_path.suffix.lower(), "image/jpeg")

    def iter_file() -> Iterator[bytes]:
        with file_path.open("rb") as file:
            yield from file

    return StreamingResponse(iter_file(), media_type=media_type)


__all__ = ["router"]
