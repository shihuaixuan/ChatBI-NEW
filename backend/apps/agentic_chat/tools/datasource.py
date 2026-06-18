from apps.agentic_chat.schemas import ToolResult


class DatasourceTool:
    name = "datasource.get"

    def __init__(self, session):
        self.session = session

    def run(self, payload: dict) -> ToolResult:
        from apps.datasource.crud.datasource import get_ds

        datasource_id = payload.get("datasource_id")
        ds = get_ds(self.session, datasource_id) if datasource_id else None
        if not ds:
            return ToolResult(success=False, error_code="datasource_not_found", message="数据源不存在")
        return ToolResult(success=True, payload={"id": ds.id, "name": ds.name, "type": ds.type})
