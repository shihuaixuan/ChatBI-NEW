from io import BytesIO

import pandas as pd  # type: ignore[import-untyped]
from python_calamine import CalamineError

from apps.semantic.errors import SemanticValidationError
from apps.semantic.models.dto.term_excel import TermWorkbookRow
from apps.semantic.repository.term_workbook_repository import (
    TermWorkbookRepository,
)

_COLUMNS = (
    "domain_id",
    "name",
    "aliases",
    "description",
    "dataset_ids",
    "enabled",
)


class ExcelTermWorkbookRepository(TermWorkbookRepository):
    """使用 Excel 文件读写术语工作簿。"""

    def read(self, content: bytes) -> list[TermWorkbookRow]:
        rows: list[TermWorkbookRow] = []
        try:
            with pd.ExcelFile(BytesIO(content), engine="calamine") as workbook:
                for sheet_name in workbook.sheet_names:
                    frame = workbook.parse(
                        sheet_name=sheet_name,
                        dtype=str,
                    ).fillna("")
                    missing_columns = [
                        column
                        for column in _COLUMNS
                        if column not in frame.columns
                    ]
                    if missing_columns:
                        raise SemanticValidationError(
                            "SEMANTIC_TERM_EXCEL_COLUMNS_INVALID:"
                            + ",".join(missing_columns)
                        )
                    for index, raw in frame.iterrows():
                        values = {
                            column: str(raw[column]).strip()
                            for column in _COLUMNS
                        }
                        if not any(values.values()):
                            continue
                        rows.append(
                            TermWorkbookRow(
                                row_number=int(index) + 2,
                                **values,
                            )
                        )
        except (CalamineError, ValueError) as error:
            raise SemanticValidationError(
                "SEMANTIC_TERM_EXCEL_FILE_INVALID"
            ) from error
        return rows

    def write(
        self,
        rows: list[TermWorkbookRow],
        *,
        include_error: bool = False,
    ) -> bytes:
        columns = [*_COLUMNS, "error"] if include_error else list(_COLUMNS)
        data = [
            {
                column: getattr(row, column)
                for column in columns
            }
            for row in rows
        ]
        frame = pd.DataFrame(data, columns=columns)
        output = BytesIO()
        with pd.ExcelWriter(
            output,
            engine="xlsxwriter",
            engine_kwargs={"options": {"strings_to_numbers": False}},
        ) as writer:
            frame.to_excel(writer, sheet_name="Terms", index=False)
        return output.getvalue()


__all__ = ["ExcelTermWorkbookRepository"]
