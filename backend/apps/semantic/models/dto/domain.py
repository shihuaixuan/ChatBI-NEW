from apps.semantic.models.dto.base import SemanticBaseDTO


class DomainPayload(SemanticBaseDTO):
    name: str
    biz_name: str
    description: str | None = None
    parent_id: int | None = None
    admin: str | None = None
    owner: str | None = None
