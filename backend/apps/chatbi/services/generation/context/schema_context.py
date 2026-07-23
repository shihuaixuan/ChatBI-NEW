import json
from typing import Protocol

from apps.access_control.models.dto import DataPolicySubject, UserInfoDTO
from apps.access_control.services import DataPolicyService
from apps.chatbi.models import (
    GenerationSchemaContext,
    GenerationSchemaTableCandidate,
)
from apps.datasource import PhysicalRelationCell, PhysicalTableDetail
from apps.datasource.services import (
    DatasourceConnectionService,
    DatasourceMetadataService,
    DatasourceService,
)

_NO_SCHEMA_TYPES = frozenset(
    {"mysql", "es", "sqlite", "hive", "doris", "starrocks"}
)


class SchemaRankingClient(Protocol):
    """生成 Schema 的物理表相关性排序端口。"""

    def rank(
        self,
        question: str,
        candidates: list[GenerationSchemaTableCandidate],
        *,
        limit: int,
    ) -> list[int]: ...


class SchemaContextService:
    """从公开领域 Service 构造 SQL 生成需要的物理 Schema 上下文。"""

    def __init__(
        self,
        datasource_service: DatasourceService,
        metadata_service: DatasourceMetadataService,
        connection_service: DatasourceConnectionService,
        data_policy_service: DataPolicyService,
        table_ranker: SchemaRankingClient,
        *,
        embedding_enabled: bool,
        embedding_limit: int,
    ) -> None:
        if embedding_limit <= 0:
            raise ValueError("GENERATION_SCHEMA_EMBEDDING_LIMIT_INVALID")
        self._datasource_service = datasource_service
        self._metadata_service = metadata_service
        self._connection_service = connection_service
        self._data_policy_service = data_policy_service
        self._table_ranker = table_ranker
        self._embedding_enabled = embedding_enabled
        self._embedding_limit = embedding_limit

    def build(
        self,
        current_user: UserInfoDTO,
        datasource_id: int,
        question: str,
        *,
        embedding: bool = True,
        table_names: list[str] | None = None,
        include_sample_data: bool = True,
    ) -> GenerationSchemaContext:
        datasource = self._datasource_service.get(datasource_id)
        details = [
            detail
            for detail in self._metadata_service.get_schema(datasource_id)
            if detail.table.checked and detail.table.id is not None
        ]
        if not details:
            return GenerationSchemaContext(schema="")

        policy = self._data_policy_service.resolve(
            DataPolicySubject(
                user_id=current_user.id,
                workspace_id=current_user.oid,
                is_system_admin=current_user.isAdmin,
                name=current_user.name,
                account=current_user.account,
                email=current_user.email,
                variable_assignments=current_user.system_variables or [],
            ),
            datasource_id,
            table_names=[detail.table.table_name for detail in details],
        )
        if not policy.allowed:
            raise PermissionError(policy.reason)
        denied_field_ids = {item.field_id for item in policy.denied_columns}

        requested_names = set(table_names) if table_names is not None else None
        visible_details: list[PhysicalTableDetail] = []
        for detail in details:
            if (
                requested_names is not None
                and detail.table.table_name not in requested_names
            ):
                continue
            visible_details.append(
                detail.model_copy(
                    update={
                        "fields": [
                            field
                            for field in detail.fields
                            if field.checked
                            and field.id not in denied_field_ids
                        ]
                    }
                )
            )

        database_name = self._connection_service.get_database_name(datasource_id)
        schema_header = f"【DB_ID】 {database_name}\n【Schema】\n"
        if not visible_details:
            return GenerationSchemaContext(schema=schema_header)

        schema_by_table_id: dict[int, str] = {}
        detail_by_table_id: dict[int, PhysicalTableDetail] = {}
        for detail in visible_details:
            table = detail.table
            if table.id is None:
                continue
            detail_by_table_id[table.id] = detail
            qualified_name = (
                f"{database_name}.{table.table_name}"
                if datasource.type not in _NO_SCHEMA_TYPES and database_name
                else table.table_name
            )
            comment = (table.custom_comment or "").strip()
            schema_table = f"# Table: {qualified_name}"
            schema_table += f", {comment}\n[\n" if comment else "\n[\n"
            schema_table += ",\n".join(
                (
                    f"({field.field_name}:{field.field_type}, "
                    f"{(field.custom_comment or '').strip()})"
                    if (field.custom_comment or "").strip()
                    else f"({field.field_name}:{field.field_type})"
                )
                for field in detail.fields
            )
            schema_by_table_id[table.id] = schema_table + "\n]\n"

        ordered_table_ids = list(detail_by_table_id)
        selected_table_ids = ordered_table_ids
        if embedding and self._embedding_enabled:
            ranked_ids = self._table_ranker.rank(
                question,
                [
                    GenerationSchemaTableCandidate(
                        table_id=table_id,
                        embedding=detail_by_table_id[table_id].table.embedding,
                    )
                    for table_id in ordered_table_ids
                ],
                limit=self._embedding_limit,
            )
            selected_table_ids = [
                table_id
                for table_id in ranked_ids
                if table_id in detail_by_table_id
            ]

        visible_field_names = {
            field.id: field.field_name
            for detail in visible_details
            for field in detail.fields
            if field.id is not None
        }
        relations = self._visible_relations(
            datasource.table_relation or [],
            set(detail_by_table_id),
            set(visible_field_names),
            set(selected_table_ids),
        )
        related_table_ids = {
            endpoint.cell
            for relation in relations
            for endpoint in (relation.source, relation.target)
            if endpoint is not None
        }
        final_table_ids = selected_table_ids + [
            table_id
            for table_id in ordered_table_ids
            if table_id in related_table_ids and table_id not in selected_table_ids
        ]
        schema = schema_header + "".join(
            schema_by_table_id[table_id] for table_id in final_table_ids
        )
        if relations:
            table_names_by_id = {
                table_id: detail.table.table_name
                for table_id, detail in detail_by_table_id.items()
            }
            relation_lines = [
                (
                    f"{table_names_by_id[relation.source.cell]}."
                    f"{visible_field_names[relation.source.port]}="
                    f"{table_names_by_id[relation.target.cell]}."
                    f"{visible_field_names[relation.target.port]}"
                )
                for relation in relations
                if relation.source is not None and relation.target is not None
            ]
            if relation_lines:
                schema += "【Foreign keys】\n" + "\n".join(relation_lines) + "\n"

        sample_data = ""
        if include_sample_data:
            samples: list[str] = []
            for detail in visible_details:
                field_names = [field.field_name for field in detail.fields]
                if not field_names:
                    continue
                rows = self._connection_service.sample_rows(
                    datasource_id,
                    detail.table.table_name,
                    field_names,
                    limit=3,
                )
                if rows:
                    normalized_rows = [
                        {
                            key: self._normalize_sample_value(value)
                            for key, value in row.items()
                        }
                        for row in rows
                    ]
                    samples.append(
                        f"# Table: {detail.table.table_name}\n"
                        + json.dumps(
                            normalized_rows,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
            sample_data = "\n".join(samples)

        return GenerationSchemaContext(schema=schema, sample_data=sample_data)

    @staticmethod
    def _visible_relations(
        raw_relations: list[object],
        visible_table_ids: set[int],
        visible_field_ids: set[int],
        selected_table_ids: set[int],
    ) -> list[PhysicalRelationCell]:
        relations: list[PhysicalRelationCell] = []
        for raw_relation in raw_relations:
            relation = PhysicalRelationCell.model_validate(raw_relation)
            if (
                relation.shape != "edge"
                or relation.source is None
                or relation.target is None
                or relation.source.cell not in visible_table_ids
                or relation.target.cell not in visible_table_ids
                or relation.source.port not in visible_field_ids
                or relation.target.port not in visible_field_ids
                or (
                    relation.source.cell not in selected_table_ids
                    and relation.target.cell not in selected_table_ids
                )
            ):
                continue
            relations.append(relation)
        return relations

    @staticmethod
    def _normalize_sample_value(value: object) -> object:
        if isinstance(value, str):
            normalized = value.replace("\n", " ").replace("\r", " ")
            return normalized[:100] + "..." if len(normalized) > 100 else normalized
        return value


__all__ = [
    "SchemaContextService",
    "SchemaRankingClient",
]
