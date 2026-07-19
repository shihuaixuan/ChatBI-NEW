from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DynamicSQLSubqueryMapping:
    table: str
    query: str


@dataclass(frozen=True, slots=True)
class DynamicSQLGenerationData:
    """动态 SQL 重写所需的稳定业务输入。"""

    sql: str
    subqueries: list[DynamicSQLSubqueryMapping] = field(default_factory=list)
    language: str = "简体中文"
    engine: str = ""
    assistant_name: str = "Numora"


__all__ = ["DynamicSQLGenerationData", "DynamicSQLSubqueryMapping"]
