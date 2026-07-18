"""Assistant 审计资源公开查询。"""

from sqlalchemy import String, func, literal_column, select
from sqlalchemy.sql import Select
from sqlmodel import col

from apps.assistant.models.orm import AssistantModel


def build_assistant_audit_resource_query() -> Select[tuple[str, str, str]]:
    return select(
        func.cast(col(AssistantModel.id), String).label("id"),
        col(AssistantModel.name).label("name"),
        literal_column("'application'").label("module"),
    ).select_from(AssistantModel)
