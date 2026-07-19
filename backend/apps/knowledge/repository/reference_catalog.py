from sqlmodel import Session

from apps.assistant.public import list_assistant_references
from apps.datasource import build_datasource_catalog


class PublicSQLExampleReferenceCatalog:
    """通过 Datasource 和 Assistant 公开目录解析展示名称。"""

    def __init__(self, session: Session) -> None:
        self._datasource_catalog = build_datasource_catalog(session)

    def datasource_names(
        self,
        workspace_id: int,
        datasource_ids: list[int] | None = None,
    ) -> dict[int, str]:
        return {
            int(item.id): item.name
            for item in self._datasource_catalog.list_for_workspace(
                workspace_id,
                datasource_ids,
            )
        }

    def assistant_names(
        self,
        workspace_id: int,
        assistant_ids: list[int] | None = None,
    ) -> dict[int, str]:
        return {
            item.id: item.name
            for item in list_assistant_references(
                assistant_ids,
                workspace_id=workspace_id,
                assistant_type=1,
            )
        }
