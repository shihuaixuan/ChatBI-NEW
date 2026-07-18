"""Assistant 仓储端口。"""

from typing import Protocol

from apps.assistant.models.dto import (
    AssistantCreateData,
    AssistantRecord,
    AssistantReference,
    AssistantUpdateData,
)


class AssistantRepository(Protocol):
    def get(self, assistant_id: int) -> AssistantRecord | None: ...

    def get_by_app_id(self, app_id: str) -> AssistantRecord | None: ...

    def list_for_workspace(
        self,
        workspace_id: int,
        *,
        assistant_type: int | None = None,
        exclude_type: int | None = None,
    ) -> list[AssistantRecord]: ...

    def list_references(
        self,
        assistant_ids: list[int] | None = None,
        *,
        workspace_id: int | None = None,
        assistant_type: int | None = None,
    ) -> list[AssistantReference]: ...

    def list_domains(self) -> list[str]: ...

    def create(self, data: AssistantCreateData) -> AssistantRecord: ...

    def update(
        self,
        assistant_id: int,
        data: AssistantUpdateData,
    ) -> AssistantRecord | None: ...

    def update_configuration(
        self,
        assistant_id: int,
        configuration: str,
    ) -> AssistantRecord | None: ...

    def delete(self, assistant_id: int) -> AssistantRecord | None: ...
