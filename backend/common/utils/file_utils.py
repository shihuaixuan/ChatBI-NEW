"""项目内统一文件存储工具。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from starlette.datastructures import UploadFile

from common.core.config import settings


class SQLBotFileUtils:
    """在配置的上传目录内校验、保存和删除文件。"""

    @staticmethod
    def get_file_path(file_id: str) -> str:
        safe_file_id = Path(file_id).name
        if not safe_file_id or safe_file_id != file_id:
            raise ValueError("文件标识不合法")
        return str(Path(settings.UPLOAD_DIR) / safe_file_id)

    @staticmethod
    def split_filename_and_flag(combined_name: str | None) -> tuple[str, str]:
        if not combined_name:
            raise ValueError("文件名不能为空")
        file_name, separator, flag_name = combined_name.rpartition(",")
        if not separator or not file_name or not flag_name:
            raise ValueError("文件名必须包含用途标识")
        if Path(file_name).name != file_name:
            raise ValueError("文件名不合法")
        return file_name, flag_name

    @staticmethod
    def check_file(
        file: UploadFile,
        file_types: list[str] | None = None,
        limit_file_size: int | None = 200 * 1024,
    ) -> None:
        file_name = file.filename or ""
        suffix = Path(file_name).suffix.lower()
        normalized_types = {
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in (file_types or [".jpg", ".jpeg", ".png", ".svg"])
        }
        if suffix not in normalized_types:
            raise ValueError(f"不支持的文件类型: {suffix or '无扩展名'}")

        size = file.size
        if size is None:
            current_position = file.file.tell()
            file.file.seek(0, os.SEEK_END)
            size = file.file.tell()
            file.file.seek(current_position)
        if limit_file_size is not None and size > limit_file_size:
            raise ValueError("文件大小超过限制")

        current_position = file.file.tell()
        file.file.seek(0)
        header = file.file.read(512)
        file.file.seek(current_position)
        if not SQLBotFileUtils._header_matches(suffix, header):
            raise ValueError("文件内容与扩展名不匹配")

    @staticmethod
    async def upload(file: UploadFile) -> str:
        suffix = Path(file.filename or "").suffix.lower()
        file_id = f"{uuid.uuid4().hex}{suffix}"
        upload_dir = Path(settings.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / file_id
        file.file.seek(0)
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
        return file_id

    @staticmethod
    def delete_file(file_id: str) -> None:
        file_path = Path(SQLBotFileUtils.get_file_path(file_id))
        try:
            file_path.unlink()
        except FileNotFoundError:
            return

    @staticmethod
    def _header_matches(suffix: str, header: bytes) -> bool:
        if suffix in {".jpg", ".jpeg"}:
            return header.startswith(b"\xff\xd8\xff")
        if suffix == ".png":
            return header.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix == ".svg":
            text = header.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
            return text.startswith(b"<svg") or b"<svg" in text
        return True


__all__ = ["SQLBotFileUtils"]
