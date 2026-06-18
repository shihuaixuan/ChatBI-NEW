from sqlalchemy import select

from apps.agentic_chat.schemas import ToolResult
from apps.datasource.models.datasource import CoreField, CoreTable


class SchemaTool:
    name = "schema.search"

    def __init__(self, session):
        self.session = session

    def run(self, payload: dict) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        if not datasource_id:
            return ToolResult(success=False, error_code="datasource_required", message="缺少数据源")
        tables = self.session.exec(
            select(CoreTable).where(CoreTable.ds_id == datasource_id, CoreTable.checked.is_(True))
        ).scalars().all()
        items = []
        for table in tables:
            fields = self.session.exec(
                select(CoreField).where(CoreField.table_id == table.id, CoreField.checked.is_(True))
            ).scalars().all()
            items.append(
                {
                    "table": table.table_name,
                    "comment": table.custom_comment or table.table_comment,
                    "fields": [
                        {
                            "name": field.field_name,
                            "type": field.field_type,
                            "comment": field.custom_comment or field.field_comment,
                        }
                        for field in fields
                    ],
                }
            )
        return ToolResult(success=True, payload={"items": items, "allowed_tables": [item["table"] for item in items]})
