"""Datasource 领域对外提供的稳定数据契约。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class DatasourceRecommendationConfigStore(Protocol):
    """推荐问题事务使用的数据源配置公开端口。"""

    def get_recommended_config(self, datasource_id: int) -> int | None: ...

    def stage_recommended_config(
        self,
        datasource_id: int,
        recommended_config: int,
    ) -> bool: ...


class DatasourceSummary(BaseModel):
    """Assistant 等调用方可读取的数据源摘要。"""

    id: int | str
    name: str
    description: str | None = None
    type: str | None = None
    type_name: str | None = None
    num: int | str | None = None


class ExternalDatasourceField(BaseModel):
    id: int | None = None
    name: str | None = None
    type: str | None = None
    comment: str | None = None


class ExternalDatasourceTable(BaseModel):
    id: int | None = None
    name: str | None = None
    comment: str | None = None
    rule: str | None = None
    sql: str | None = None
    fields: list[ExternalDatasourceField] | None = None


class ExternalDatasource(BaseModel):
    """外部来源转换后的统一数据源连接契约。"""

    id: int | None = None
    name: str
    type: str | None = None
    type_name: str | None = None
    comment: str | None = None
    description: str | None = None
    configuration: str | None = None
    host: str | None = None
    port: int | None = None
    dataBase: str | None = None
    user: str | None = None
    password: str | None = None
    db_schema: str | None = None
    extraParams: str | None = None
    mode: str | None = None
    tables: list[ExternalDatasourceTable] | None = None


class DatasourceCatalog(Protocol):
    """其他领域读取数据源范围时使用的最小目录契约。"""

    def list_for_workspace(
        self,
        workspace_id: int,
        datasource_ids: list[int] | None = None,
    ) -> list[DatasourceSummary]: ...


class DatasourcePolicyField(BaseModel):
    id: int
    name: str
    data_type: str | None = None


class DatasourcePolicyTable(BaseModel):
    id: int
    name: str
    fields: list[DatasourcePolicyField]


class DatasourcePolicySchema(BaseModel):
    id: int
    workspace_id: int
    database_type: str
    identifier_prefix: str
    identifier_suffix: str
    tables: list[DatasourcePolicyTable]


class DatasourcePolicyCatalog(Protocol):
    """Access Control 读取数据源物理标识时使用的公开契约。"""

    def get_policy_schema(
        self,
        workspace_id: int,
        datasource_id: int,
        *,
        table_names: list[str] | None = None,
        table_id: int | None = None,
    ) -> DatasourcePolicySchema | None: ...
