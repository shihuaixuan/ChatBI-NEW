from typing import Protocol

from apps.semantic.models.dto.term_excel import TermWorkbookRow


class TermWorkbookRepository(Protocol):
    """术语工作簿格式转换端口。"""

    def read(self, content: bytes) -> list[TermWorkbookRow]: ...

    def write(
        self,
        rows: list[TermWorkbookRow],
        *,
        include_error: bool = False,
    ) -> bytes: ...
