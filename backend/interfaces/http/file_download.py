import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from common.core.config import settings
from common.core.file import FileRequest
from common.interfaces.i18n import PLACEHOLDER_PREFIX

router = APIRouter(tags=["System"], prefix="/system")

path = settings.EXCEL_PATH


@router.post("/download-fail-info", summary=f"{PLACEHOLDER_PREFIX}download-fail-info")
async def download_excel(req: FileRequest) -> FileResponse:
    """
    根据文件路径下载 Excel 文件
    """
    filename = req.file
    file_path = os.path.join(path, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File Not Exists")

    if not filename.endswith("_error.xlsx"):
        raise HTTPException(status_code=400, detail="Only support _error.xlsx")

    filename = os.path.basename(file_path)

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
