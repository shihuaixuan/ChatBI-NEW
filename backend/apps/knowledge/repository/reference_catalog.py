from sqlmodel import Session

from apps.assistant.public import list_assistant_references
from apps.datasource import build_datasource_catalog
from apps.knowledge.models.dto import SQLExampleDatasetScope
from apps.semantic.composition import build_semantic_dataset_reference_service


class PublicSQLExampleReferenceCatalog:
    """通过 Datasource 和 Assistant 公开目录解析展示名称。"""

    def __init__(self, session: Session) -> None:
        self._datasource_catalog = build_datasource_catalog(session)
        self._semantic_catalog = build_semantic_dataset_reference_service(session)

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

    def dataset_scope(
        self,
        workspace_id: int,
        dataset_id: int,
    ) -> SQLExampleDatasetScope | None:
        reference = self._semantic_catalog.get(workspace_id, dataset_id)
        if reference is None:
            return None
        return SQLExampleDatasetScope(
            dataset_id=reference.dataset_id,
            datasource_ids=reference.datasource_ids,
            metric_ids=reference.metric_ids,
            dimension_ids=reference.dimension_ids,
        )
