from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PermissionSQLFilter:
    table: str
    condition: str


@dataclass(frozen=True, slots=True)
class PermissionSQLGenerationData:
    """权限 SQL 重写所需的稳定业务输入。"""

    record_id: int
    sql: str
    filters: list[PermissionSQLFilter] = field(default_factory=list)
    language: str = "简体中文"
    engine: str = ""
    assistant_name: str = "Numora"


__all__ = ["PermissionSQLFilter", "PermissionSQLGenerationData"]
