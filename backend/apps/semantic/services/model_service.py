from __future__ import annotations

from apps.semantic.errors import (
    SemanticForbiddenError,
    SemanticNotFoundError,
    SemanticValidationError,
)
from apps.semantic.models.dto import (
    ModelBuildSchemaPayload,
    ModelBuildSchemaResult,
    ModelCreateWithAssetsPayload,
    ModelPayload,
)
from apps.semantic.models.orm import SemanticModel
from apps.semantic.repository.datasource_metadata_repository import (
    DatasourceMetadataRepository,
)
from apps.semantic.repository.domain_repository import DomainRepository
from apps.semantic.repository.model_repository import ModelRepository
from apps.semantic.services.builders.model_builder import (
    SemanticModelBuilder,
    build_model_with_assets,
)
from apps.semantic.utils.model_update import assign_values
from apps.semantic.utils.orm_mapping import normalize_model_source


class SemanticModelService:
    """模型本体及建模流程的应用服务。"""

    def __init__(
        self,
        repository: ModelRepository,
        domain_reader: DomainRepository,
        datasource_reader: DatasourceMetadataRepository,
    ):
        self._repository = repository
        self._domain_reader = domain_reader
        self._datasource_reader = datasource_reader

    def list_models(
        self,
        oid: int,
        domain_id: int | None = None,
    ) -> list[SemanticModel]:
        return self._repository.list_active(oid, domain_id)

    def create_model(self, oid: int, payload: ModelPayload) -> SemanticModel:
        self._ensure_domain(oid, payload.domain_id)
        self._ensure_datasource(oid, payload.datasource_id)
        model = SemanticModel(**payload.model_dump(), oid=oid)
        normalize_model_source(model)
        return self._repository.create(model)

    def update_model(
        self,
        oid: int,
        model_id: int,
        payload: ModelPayload,
    ) -> SemanticModel:
        model = self._repository.get_active(oid, model_id)
        if model is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_NOT_FOUND")
        self._ensure_domain(oid, payload.domain_id)
        self._ensure_datasource(oid, payload.datasource_id)
        assign_values(model, payload.model_dump())
        normalize_model_source(model)
        return self._repository.update(model)

    def delete_model(self, oid: int, model_id: int) -> dict[str, int | bool]:
        model = self._repository.get_active(oid, model_id)
        if model is None:
            raise SemanticNotFoundError("SEMANTIC_MODEL_NOT_FOUND")
        self._repository.delete(model)
        return {"id": model_id, "deleted": True}

    def build_model_schema(
        self,
        oid: int,
        payload: ModelBuildSchemaPayload,
    ) -> ModelBuildSchemaResult:
        self._ensure_datasource(oid, payload.datasource_id)
        columns = payload.columns
        if not columns and payload.table_name:
            columns = self._datasource_reader.list_columns(
                payload.datasource_id,
                payload.table_name,
            )
        if not columns:
            raise SemanticValidationError("SEMANTIC_MODEL_COLUMNS_REQUIRED")
        return SemanticModelBuilder().build_table_schema(
            table_name=payload.table_name,
            columns=columns,
            source_type=payload.source_type,
            sql=payload.sql,
            datasource_id=payload.datasource_id,
        )

    def create_model_with_assets(
        self,
        oid: int,
        payload: ModelCreateWithAssetsPayload,
    ) -> dict[str, object]:
        self._ensure_domain(oid, payload.domain_id)
        self._ensure_datasource(oid, payload.datasource_id)
        effective_payload = payload
        if not payload.model_detail and payload.table_name:
            columns = self._datasource_reader.list_columns(
                payload.datasource_id,
                payload.table_name,
            )
            model_detail = (
                SemanticModelBuilder()
                .build_table_schema(
                    table_name=payload.table_name,
                    columns=columns,
                    source_type=payload.source_type,
                    sql=payload.sql,
                    datasource_id=payload.datasource_id,
                )
                .model_detail
            )
            effective_payload = payload.model_copy(
                update={"model_detail": model_detail}
            )
        bundle = self._repository.create_with_assets(
            build_model_with_assets(effective_payload, oid=oid)
        )
        return {
            "model": bundle.model,
            "dimensions": bundle.dimensions,
            "metrics": bundle.metrics,
        }

    def _ensure_domain(self, oid: int, domain_id: int) -> None:
        if not self._domain_reader.is_active(oid, domain_id):
            raise SemanticNotFoundError("SEMANTIC_DOMAIN_NOT_FOUND")

    def _ensure_datasource(self, oid: int, datasource_id: int) -> None:
        if not self._datasource_reader.is_accessible(oid, datasource_id):
            raise SemanticForbiddenError("SEMANTIC_DATASOURCE_NOT_FOUND")
