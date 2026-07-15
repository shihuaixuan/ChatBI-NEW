from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from apps.datasource.models.datasource import CoreDatasource, CoreField, CoreTable
from apps.headless.asset_document import HeadlessAssetDocumentBuilder
from apps.headless.asset_relation import (
    build_aliases_for_asset,
    build_dataset_asset_relations,
    build_dimension_relations,
    build_dimension_value_relations,
    build_metric_relations,
    build_term_relations,
)
from apps.headless.metric_embedding import rebuild_dataset_metric_embeddings
from apps.headless.models import (
    HeadlessAssetAlias,
    HeadlessAssetDocument,
    HeadlessAssetEmbedding,
    HeadlessAssetRelation,
    HeadlessDataSet,
    HeadlessDataSetAsset,
    HeadlessDataSetModelConfig,
    HeadlessDimension,
    HeadlessDimensionValue,
    HeadlessDomain,
    HeadlessMetric,
    HeadlessModel,
    HeadlessModelField,
    HeadlessModelMeasure,
    HeadlessModelRelation,
    HeadlessSchemaIndex,
    HeadlessTerm,
)
from apps.headless.schemas import (
    DataSetPayload,
    DimensionPayload,
    DomainPayload,
    HeadlessColumnMeta,
    HeadlessTableMeta,
    MetricBatchCreateFromMeasuresPayload,
    MetricEmbeddingRebuildResponse,
    MetricEmbeddingStatusResponse,
    MetricPayload,
    ModelBuildSchemaPayload,
    ModelCreateWithAssetsPayload,
    ModelPayload,
    ModelRelationPayload,
    SchemaMapInfo,
    SchemaMapRequest,
    TermPayload,
)
from apps.headless.service import (
    HeadlessModelBuilder,
    HeadlessSchemaBuilder,
    HeadlessSchemaMapper,
    build_metrics_from_model_measures,
    build_model_with_assets,
    build_ontology_from_schema,
)
from apps.headless.storage_sync import (
    dataset_assets_from_detail,
    dataset_model_configs_from_detail,
    dimension_values_from_maps,
    model_fields_from_detail,
    model_measures_from_detail,
    normalize_model_source,
    normalize_model_storage_fields,
)
from apps.retrieval.headless_indexing import HeadlessIndexCoordinator
from common.core.deps import CurrentUser, SessionDep

router = APIRouter(tags=["Headless"], prefix="/headless")


@router.get("/datasources")
async def list_datasources(session: SessionDep, current_user: CurrentUser):
    return _all(
        session.exec(select(CoreDatasource).where(CoreDatasource.oid == current_user.oid).order_by(CoreDatasource.id))
    )


@router.get("/datasources/{datasource_id}/tables", response_model=list[HeadlessTableMeta])
async def list_datasource_tables(session: SessionDep, current_user: CurrentUser, datasource_id: int):
    _ensure_datasource(session, current_user.oid, datasource_id)
    persisted_tables = _all(
        session.exec(select(CoreTable).where(CoreTable.ds_id == datasource_id).order_by(CoreTable.table_name))
    )
    if persisted_tables:
        return _table_metas_from_persisted_tables(persisted_tables)

    try:
        from apps.db.db import get_tables

        datasource = session.get(CoreDatasource, datasource_id)
        live_tables = get_tables(datasource)
        return [
            HeadlessTableMeta(
                table_name=item.tableName,
                table_comment=item.tableComment,
            )
            for item in live_tables
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"HEADLESS_TABLE_DISCOVERY_FAILED: {exc}") from exc


@router.get("/datasources/{datasource_id}/tables/{table_name}/columns", response_model=list[HeadlessColumnMeta])
async def list_datasource_columns(session: SessionDep, current_user: CurrentUser, datasource_id: int, table_name: str):
    _ensure_datasource(session, current_user.oid, datasource_id)
    table = _first_entity(
        session.exec(select(CoreTable).where(CoreTable.ds_id == datasource_id, CoreTable.table_name == table_name))
    )
    if table is not None:
        if not table.checked:
            return []
        persisted_fields = _all(
            session.exec(select(CoreField).where(CoreField.table_id == table.id).order_by(CoreField.field_index))
        )
        return _column_metas_from_persisted_fields(persisted_fields)

    try:
        from apps.db.db import get_fields

        datasource = session.get(CoreDatasource, datasource_id)
        live_fields = get_fields(datasource, table_name)
        return [
            HeadlessColumnMeta(
                field_name=item.fieldName,
                field_type=item.fieldType,
                field_comment=item.fieldComment,
                field_index=index,
            )
            for index, item in enumerate(live_fields)
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"HEADLESS_COLUMN_DISCOVERY_FAILED: {exc}") from exc


@router.get("/domains")
async def list_domains(session: SessionDep, current_user: CurrentUser):
    return _all(
        session.exec(
            select(HeadlessDomain)
            .where(HeadlessDomain.oid == current_user.oid, HeadlessDomain.status == 1)
            .order_by(HeadlessDomain.id)
        )
    )


@router.post("/domains")
async def create_domain(session: SessionDep, current_user: CurrentUser, payload: DomainPayload):
    domain = HeadlessDomain(**payload.model_dump(), oid=current_user.oid)
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


@router.put("/domains/{domain_id}")
async def update_domain(session: SessionDep, current_user: CurrentUser, domain_id: int, payload: DomainPayload):
    domain = _get_active(session, HeadlessDomain, current_user.oid, domain_id, "HEADLESS_DOMAIN_NOT_FOUND")
    _assign_payload(domain, payload.model_dump())
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


@router.delete("/domains/{domain_id}")
async def delete_domain(session: SessionDep, current_user: CurrentUser, domain_id: int):
    domain = _get_active(session, HeadlessDomain, current_user.oid, domain_id, "HEADLESS_DOMAIN_NOT_FOUND")
    dataset_ids = _all(
        session.exec(
            select(HeadlessDataSet.id).where(HeadlessDataSet.oid == current_user.oid, HeadlessDataSet.domain_id == domain_id)
        )
    )
    model_ids = _all(
        session.exec(
            select(HeadlessModel.id).where(HeadlessModel.oid == current_user.oid, HeadlessModel.domain_id == domain_id)
        )
    )
    if dataset_ids:
        session.exec(
            delete(HeadlessSchemaIndex).where(
                HeadlessSchemaIndex.oid == current_user.oid,
                HeadlessSchemaIndex.dataset_id.in_(dataset_ids),
            )
        )
    if model_ids:
        session.exec(
            delete(HeadlessMetric).where(
                HeadlessMetric.oid == current_user.oid,
                HeadlessMetric.model_id.in_(model_ids),
            )
        )
        session.exec(
            delete(HeadlessDimension).where(
                HeadlessDimension.oid == current_user.oid,
                HeadlessDimension.model_id.in_(model_ids),
            )
        )
    session.exec(delete(HeadlessDataSet).where(HeadlessDataSet.oid == current_user.oid, HeadlessDataSet.domain_id == domain_id))
    session.exec(delete(HeadlessTerm).where(HeadlessTerm.oid == current_user.oid, HeadlessTerm.domain_id == domain_id))
    session.exec(delete(HeadlessModel).where(HeadlessModel.oid == current_user.oid, HeadlessModel.domain_id == domain_id))
    session.delete(domain)
    session.commit()
    return {"id": domain_id, "deleted": True}


@router.get("/models")
async def list_models(session: SessionDep, current_user: CurrentUser, domain_id: int | None = None):
    conditions = [HeadlessModel.oid == current_user.oid, HeadlessModel.status == 1]
    if domain_id is not None:
        conditions.append(HeadlessModel.domain_id == domain_id)
    return _all(session.exec(select(HeadlessModel).where(*conditions).order_by(HeadlessModel.id)))


@router.post("/models")
async def create_model(session: SessionDep, current_user: CurrentUser, payload: ModelPayload):
    _ensure_domain(session, current_user.oid, payload.domain_id)
    _ensure_datasource(session, current_user.oid, payload.datasource_id)
    model = HeadlessModel(**payload.model_dump(), oid=current_user.oid)
    normalize_model_source(model)
    session.add(model)
    session.flush()
    session.refresh(model)
    _sync_model_storage(session, model)
    session.commit()
    session.refresh(model)
    return model


@router.put("/models/{model_id}")
async def update_model(session: SessionDep, current_user: CurrentUser, model_id: int, payload: ModelPayload):
    model = _get_active(session, HeadlessModel, current_user.oid, model_id, "HEADLESS_MODEL_NOT_FOUND")
    _ensure_domain(session, current_user.oid, payload.domain_id)
    _ensure_datasource(session, current_user.oid, payload.datasource_id)
    _assign_payload(model, payload.model_dump())
    normalize_model_source(model)
    session.add(model)
    _sync_model_storage(session, model)
    session.commit()
    session.refresh(model)
    return model


@router.delete("/models/{model_id}")
async def delete_model(session: SessionDep, current_user: CurrentUser, model_id: int):
    model = _get_active(session, HeadlessModel, current_user.oid, model_id, "HEADLESS_MODEL_NOT_FOUND")
    _clear_domain_schema_indexes(session, current_user.oid, model.domain_id)
    session.exec(
        delete(HeadlessModelRelation).where(
            HeadlessModelRelation.oid == current_user.oid,
            (HeadlessModelRelation.left_model_id == model_id) | (HeadlessModelRelation.right_model_id == model_id),
        )
    )
    session.exec(delete(HeadlessMetric).where(HeadlessMetric.oid == current_user.oid, HeadlessMetric.model_id == model_id))
    session.exec(delete(HeadlessDimension).where(HeadlessDimension.oid == current_user.oid, HeadlessDimension.model_id == model_id))
    session.delete(model)
    session.commit()
    return {"id": model_id, "deleted": True}


@router.post("/models/build-schema")
async def build_model_schema(session: SessionDep, current_user: CurrentUser, payload: ModelBuildSchemaPayload):
    _ensure_datasource(session, current_user.oid, payload.datasource_id)
    columns = payload.columns
    if not columns and payload.table_name:
        columns = await list_datasource_columns(session, current_user, payload.datasource_id, payload.table_name)
    if not columns:
        raise HTTPException(status_code=400, detail="HEADLESS_MODEL_COLUMNS_REQUIRED")
    return HeadlessModelBuilder().build_table_schema(
        table_name=payload.table_name,
        columns=columns,
        source_type=payload.source_type,
        sql=payload.sql,
        datasource_id=payload.datasource_id,
    )


@router.post("/models/create-with-assets")
async def create_model_with_assets(session: SessionDep, current_user: CurrentUser, payload: ModelCreateWithAssetsPayload):
    _ensure_domain(session, current_user.oid, payload.domain_id)
    _ensure_datasource(session, current_user.oid, payload.datasource_id)
    if not payload.model_detail and payload.table_name:
        columns = await list_datasource_columns(session, current_user, payload.datasource_id, payload.table_name)
        payload.model_detail = HeadlessModelBuilder().build_table_schema(
            table_name=payload.table_name,
            columns=columns,
            source_type=payload.source_type,
            sql=payload.sql,
            datasource_id=payload.datasource_id,
        ).model_detail
    bundle = build_model_with_assets(payload, oid=current_user.oid)
    session.add(bundle.model)
    session.flush()
    session.refresh(bundle.model)
    for dimension in bundle.dimensions:
        dimension.model_id = bundle.model.id
        session.add(dimension)
    for metric in bundle.metrics:
        metric.model_id = bundle.model.id
        session.add(metric)
    _sync_model_storage(session, bundle.model)
    session.commit()
    session.refresh(bundle.model)
    return {
        "model": bundle.model,
        "dimensions": bundle.dimensions,
        "metrics": bundle.metrics,
    }


@router.get("/model-relations")
async def list_model_relations(session: SessionDep, current_user: CurrentUser, domain_id: int | None = None):
    conditions = [HeadlessModelRelation.oid == current_user.oid, HeadlessModelRelation.status == 1]
    if domain_id is not None:
        conditions.append(HeadlessModelRelation.domain_id == domain_id)
    return _all(session.exec(select(HeadlessModelRelation).where(*conditions).order_by(HeadlessModelRelation.id)))


@router.post("/model-relations")
async def create_model_relation(session: SessionDep, current_user: CurrentUser, payload: ModelRelationPayload):
    _ensure_relation_models(session, current_user.oid, payload)
    relation = HeadlessModelRelation(**payload.model_dump(), oid=current_user.oid)
    session.add(relation)
    session.commit()
    session.refresh(relation)
    return relation


@router.put("/model-relations/{relation_id}")
async def update_model_relation(
    session: SessionDep,
    current_user: CurrentUser,
    relation_id: int,
    payload: ModelRelationPayload,
):
    relation = _get_active(
        session,
        HeadlessModelRelation,
        current_user.oid,
        relation_id,
        "HEADLESS_MODEL_RELATION_NOT_FOUND",
    )
    _ensure_relation_models(session, current_user.oid, payload)
    _assign_payload(relation, payload.model_dump())
    session.add(relation)
    session.commit()
    session.refresh(relation)
    return relation


@router.delete("/model-relations/{relation_id}")
async def delete_model_relation(session: SessionDep, current_user: CurrentUser, relation_id: int):
    relation = _get_active(
        session,
        HeadlessModelRelation,
        current_user.oid,
        relation_id,
        "HEADLESS_MODEL_RELATION_NOT_FOUND",
    )
    session.delete(relation)
    session.commit()
    return {"id": relation_id, "deleted": True}


@router.get("/metrics")
async def list_metrics(session: SessionDep, current_user: CurrentUser, model_id: int | None = None):
    conditions = [HeadlessMetric.oid == current_user.oid, HeadlessMetric.status == 1]
    if model_id is not None:
        conditions.append(HeadlessMetric.model_id == model_id)
    return _all(session.exec(select(HeadlessMetric).where(*conditions).order_by(HeadlessMetric.id)))


@router.post("/metrics")
async def create_metric(session: SessionDep, current_user: CurrentUser, payload: MetricPayload):
    _ensure_model(session, current_user.oid, payload.model_id)
    metric = HeadlessMetric(**payload.model_dump(), oid=current_user.oid)
    normalize_model_storage_fields(metric)
    _validate_metric_storage_quality(session, metric)
    session.add(metric)
    session.flush()
    session.refresh(metric)
    _sync_metric_relation_storage(session, metric)
    session.commit()
    session.refresh(metric)
    return metric


@router.post("/metrics/batch-create-from-measures")
async def batch_create_metrics_from_measures(
    session: SessionDep,
    current_user: CurrentUser,
    payload: MetricBatchCreateFromMeasuresPayload,
):
    model = _get_active(session, HeadlessModel, current_user.oid, payload.model_id, "HEADLESS_MODEL_NOT_FOUND")
    existing_metrics = _all(
        session.exec(
            select(HeadlessMetric).where(
                HeadlessMetric.oid == current_user.oid,
                HeadlessMetric.model_id == payload.model_id,
                HeadlessMetric.status == 1,
            )
        )
    )
    storage_measures = _all(
        session.exec(
            select(HeadlessModelMeasure).where(
                HeadlessModelMeasure.oid == current_user.oid,
                HeadlessModelMeasure.model_id == payload.model_id,
                HeadlessModelMeasure.status == 1,
            )
        )
    )
    result = build_metrics_from_model_measures(
        model=model,
        oid=current_user.oid,
        measure_ids=payload.measure_ids,
        measure_biz_names=payload.measure_biz_names,
        storage_measures=storage_measures,
        existing_metrics=existing_metrics,
    )
    for metric in result.metrics:
        _validate_metric_storage_quality(session, metric)
        session.add(metric)
    session.flush()
    for metric in result.metrics:
        session.refresh(metric)
        _sync_metric_relation_storage(session, metric)
    session.commit()
    for metric in result.metrics:
        session.refresh(metric)
    return {"created": result.metrics, "skipped": result.skipped}


@router.put("/metrics/{metric_id}")
async def update_metric(session: SessionDep, current_user: CurrentUser, metric_id: int, payload: MetricPayload):
    metric = _get_active(session, HeadlessMetric, current_user.oid, metric_id, "HEADLESS_METRIC_NOT_FOUND")
    _ensure_model(session, current_user.oid, payload.model_id)
    _assign_payload(metric, payload.model_dump())
    normalize_model_storage_fields(metric)
    _validate_metric_storage_quality(session, metric)
    session.add(metric)
    _sync_metric_relation_storage(session, metric)
    session.commit()
    session.refresh(metric)
    return metric


@router.delete("/metrics/{metric_id}")
async def delete_metric(session: SessionDep, current_user: CurrentUser, metric_id: int):
    metric = _get_active(session, HeadlessMetric, current_user.oid, metric_id, "HEADLESS_METRIC_NOT_FOUND")
    session.exec(
        delete(HeadlessSchemaIndex).where(
            HeadlessSchemaIndex.oid == current_user.oid,
            HeadlessSchemaIndex.element_type == "METRIC",
            HeadlessSchemaIndex.element_id == metric_id,
        )
    )
    session.delete(metric)
    session.commit()
    return {"id": metric_id, "deleted": True}


@router.get("/dimensions")
async def list_dimensions(session: SessionDep, current_user: CurrentUser, model_id: int | None = None):
    conditions = [HeadlessDimension.oid == current_user.oid, HeadlessDimension.status == 1]
    if model_id is not None:
        conditions.append(HeadlessDimension.model_id == model_id)
    return _all(session.exec(select(HeadlessDimension).where(*conditions).order_by(HeadlessDimension.id)))


@router.post("/dimensions")
async def create_dimension(session: SessionDep, current_user: CurrentUser, payload: DimensionPayload):
    _ensure_model(session, current_user.oid, payload.model_id)
    dimension = HeadlessDimension(**payload.model_dump(), oid=current_user.oid)
    session.add(dimension)
    session.flush()
    session.refresh(dimension)
    _sync_dimension_values(session, dimension)
    _sync_dimension_relation_storage(session, dimension)
    session.commit()
    session.refresh(dimension)
    return dimension


@router.put("/dimensions/{dimension_id}")
async def update_dimension(session: SessionDep, current_user: CurrentUser, dimension_id: int, payload: DimensionPayload):
    dimension = _get_active(session, HeadlessDimension, current_user.oid, dimension_id, "HEADLESS_DIMENSION_NOT_FOUND")
    _ensure_model(session, current_user.oid, payload.model_id)
    _assign_payload(dimension, payload.model_dump())
    session.add(dimension)
    _sync_dimension_values(session, dimension)
    _sync_dimension_relation_storage(session, dimension)
    session.commit()
    session.refresh(dimension)
    return dimension


@router.delete("/dimensions/{dimension_id}")
async def delete_dimension(session: SessionDep, current_user: CurrentUser, dimension_id: int):
    dimension = _get_active(session, HeadlessDimension, current_user.oid, dimension_id, "HEADLESS_DIMENSION_NOT_FOUND")
    session.exec(
        delete(HeadlessSchemaIndex).where(
            HeadlessSchemaIndex.oid == current_user.oid,
            HeadlessSchemaIndex.element_type.in_(["DIMENSION", "VALUE"]),
            HeadlessSchemaIndex.element_id == dimension_id,
        )
    )
    session.delete(dimension)
    session.commit()
    return {"id": dimension_id, "deleted": True}


@router.get("/datasets")
async def list_datasets(session: SessionDep, current_user: CurrentUser, domain_id: int | None = None):
    conditions = [HeadlessDataSet.oid == current_user.oid, HeadlessDataSet.status == 1]
    if domain_id is not None:
        conditions.append(HeadlessDataSet.domain_id == domain_id)
    return _all(session.exec(select(HeadlessDataSet).where(*conditions).order_by(HeadlessDataSet.id)))


@router.post("/datasets")
async def create_dataset(session: SessionDep, current_user: CurrentUser, payload: DataSetPayload):
    _ensure_domain(session, current_user.oid, payload.domain_id)
    dataset = HeadlessDataSet(**payload.model_dump(), oid=current_user.oid)
    session.add(dataset)
    session.flush()
    session.refresh(dataset)
    _sync_dataset_storage(session, dataset)
    session.commit()
    session.refresh(dataset)
    return dataset


@router.put("/datasets/{dataset_id}")
async def update_dataset(session: SessionDep, current_user: CurrentUser, dataset_id: int, payload: DataSetPayload):
    dataset = _get_active(session, HeadlessDataSet, current_user.oid, dataset_id, "HEADLESS_DATASET_NOT_FOUND")
    _ensure_domain(session, current_user.oid, payload.domain_id)
    _assign_payload(dataset, payload.model_dump())
    dataset.schema_version = (dataset.schema_version or 1) + 1
    session.add(dataset)
    _sync_dataset_storage(session, dataset)
    session.commit()
    session.refresh(dataset)
    return dataset


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    dataset = _get_active(session, HeadlessDataSet, current_user.oid, dataset_id, "HEADLESS_DATASET_NOT_FOUND")
    session.exec(
        delete(HeadlessSchemaIndex).where(
            HeadlessSchemaIndex.oid == current_user.oid,
            HeadlessSchemaIndex.dataset_id == dataset_id,
        )
    )
    session.delete(dataset)
    session.commit()
    return {"id": dataset_id, "deleted": True}


@router.get("/terms")
async def list_terms(session: SessionDep, current_user: CurrentUser, domain_id: int | None = None):
    conditions = [HeadlessTerm.oid == current_user.oid, HeadlessTerm.status == 1]
    if domain_id is not None:
        conditions.append(HeadlessTerm.domain_id == domain_id)
    return _all(session.exec(select(HeadlessTerm).where(*conditions).order_by(HeadlessTerm.id)))


@router.post("/terms")
async def create_term(session: SessionDep, current_user: CurrentUser, payload: TermPayload):
    _ensure_domain(session, current_user.oid, payload.domain_id)
    term = HeadlessTerm(**payload.model_dump(), oid=current_user.oid)
    session.add(term)
    session.flush()
    session.refresh(term)
    _sync_term_relation_storage(session, term)
    session.commit()
    session.refresh(term)
    return term


@router.put("/terms/{term_id}")
async def update_term(session: SessionDep, current_user: CurrentUser, term_id: int, payload: TermPayload):
    term = _get_active(session, HeadlessTerm, current_user.oid, term_id, "HEADLESS_TERM_NOT_FOUND")
    _ensure_domain(session, current_user.oid, payload.domain_id)
    _assign_payload(term, payload.model_dump())
    session.add(term)
    _sync_term_relation_storage(session, term)
    session.commit()
    session.refresh(term)
    return term


@router.delete("/terms/{term_id}")
async def delete_term(session: SessionDep, current_user: CurrentUser, term_id: int):
    term = _get_active(session, HeadlessTerm, current_user.oid, term_id, "HEADLESS_TERM_NOT_FOUND")
    session.exec(
        delete(HeadlessSchemaIndex).where(
            HeadlessSchemaIndex.oid == current_user.oid,
            HeadlessSchemaIndex.element_type == "TERM",
            HeadlessSchemaIndex.element_id == term_id,
        )
    )
    session.delete(term)
    session.commit()
    return {"id": term_id, "deleted": True}


@router.get("/datasets/{dataset_id}/schema")
async def get_dataset_schema(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    try:
        return HeadlessSchemaBuilder(session).build_dataset_schema(current_user.oid, dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/ontology")
async def get_dataset_ontology(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    try:
        schema = HeadlessSchemaBuilder(session).build_dataset_schema(current_user.oid, dataset_id)
        return build_ontology_from_schema(schema)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/schema/map", response_model=SchemaMapInfo)
async def map_schema(session: SessionDep, current_user: CurrentUser, payload: SchemaMapRequest):
    builder = HeadlessSchemaBuilder(session)
    mapper = HeadlessSchemaMapper()
    merged = SchemaMapInfo()
    for dataset_id in payload.dataset_ids:
        schema = builder.build_dataset_schema(current_user.oid, dataset_id)
        map_info = mapper.map_schema(payload.query_text, schema)
        merged.data_set_element_matches.update(map_info.data_set_element_matches)
    return merged


@router.post("/knowledge/rebuild")
async def rebuild_knowledge(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    dataset = _get_active(session, HeadlessDataSet, current_user.oid, dataset_id, "HEADLESS_DATASET_NOT_FOUND")
    dataset.index_version = (dataset.index_version or 0) + 1
    schema = HeadlessSchemaBuilder(session).build_dataset_schema(current_user.oid, dataset_id)
    session.exec(
        HeadlessSchemaIndex.__table__.delete().where(
            HeadlessSchemaIndex.oid == current_user.oid,
            HeadlessSchemaIndex.dataset_id == dataset_id,
        )
    )
    session.exec(
        HeadlessAssetDocument.__table__.delete().where(
            HeadlessAssetDocument.oid == current_user.oid,
            HeadlessAssetDocument.dataset_id == dataset_id,
        )
    )
    for element in [*schema.metrics, *schema.dimensions, *schema.dimension_values, *schema.terms]:
        session.add(
            HeadlessSchemaIndex(
                oid=current_user.oid,
                dataset_id=dataset_id,
                element_type=element.type,
                element_id=element.id,
                search_text=" ".join([element.name, element.biz_name, *(element.alias or [])]),
                payload=element.model_dump(),
            )
        )
    documents = HeadlessAssetDocumentBuilder().build_from_schema(
        schema,
        oid=current_user.oid,
        index_version=dataset.index_version,
    )
    for document in documents:
        document.updated_at = datetime.now()
        session.add(document)
    index_enqueue = HeadlessIndexCoordinator(session).enqueue_dataset_rebuild(
        tenant_id=current_user.oid,
        dataset=dataset,
    )
    session.add(dataset)
    session.commit()
    return {
        "dataset_id": dataset_id,
        "rebuilt": True,
        "document_count": len(documents),
        "schema_index_count": len([*schema.metrics, *schema.dimensions, *schema.dimension_values, *schema.terms]),
        "index_version": dataset.index_version,
        "retrieval_source_id": index_enqueue.source_id,
        "retrieval_generation": index_enqueue.generation.generation,
        "retrieval_job_ids": list(index_enqueue.generation.job_ids),
    }


@router.get("/datasets/{dataset_id}/asset-documents")
async def list_asset_documents(
    session: SessionDep,
    current_user: CurrentUser,
    dataset_id: int,
    asset_type: str | None = None,
    asset_id: int | None = None,
):
    _get_active(session, HeadlessDataSet, current_user.oid, dataset_id, "HEADLESS_DATASET_NOT_FOUND")
    conditions = [
        HeadlessAssetDocument.oid == current_user.oid,
        HeadlessAssetDocument.dataset_id == dataset_id,
    ]
    if asset_type:
        conditions.append(HeadlessAssetDocument.asset_type == asset_type)
    if asset_id is not None:
        conditions.append(HeadlessAssetDocument.asset_id == asset_id)
    return _all(session.exec(select(HeadlessAssetDocument).where(*conditions).order_by(HeadlessAssetDocument.id)))


@router.post(
    "/datasets/{dataset_id}/metric-embeddings/rebuild",
    response_model=MetricEmbeddingRebuildResponse,
)
async def rebuild_metric_embeddings(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    try:
        return rebuild_dataset_metric_embeddings(session, current_user.oid, dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/metric-embeddings", response_model=list[MetricEmbeddingStatusResponse])
async def list_metric_embeddings(session: SessionDep, current_user: CurrentUser, dataset_id: int):
    _get_active(session, HeadlessDataSet, current_user.oid, dataset_id, "HEADLESS_DATASET_NOT_FOUND")
    records = _all(
        session.exec(
            select(HeadlessAssetEmbedding)
            .where(
                HeadlessAssetEmbedding.oid == current_user.oid,
                HeadlessAssetEmbedding.dataset_id == dataset_id,
                HeadlessAssetEmbedding.asset_type == "METRIC",
            )
            .order_by(HeadlessAssetEmbedding.id)
        )
    )
    return [MetricEmbeddingStatusResponse.model_validate(record) for record in records]


def _ensure_domain(session: SessionDep, oid: int, domain_id: int) -> None:
    domain = session.get(HeadlessDomain, domain_id)
    if domain is None or domain.oid != oid or domain.status != 1:
        raise HTTPException(status_code=404, detail="HEADLESS_DOMAIN_NOT_FOUND")


def _ensure_model(session: SessionDep, oid: int, model_id: int) -> None:
    model = session.get(HeadlessModel, model_id)
    if model is None or model.oid != oid or model.status != 1:
        raise HTTPException(status_code=404, detail="HEADLESS_MODEL_NOT_FOUND")


def _ensure_datasource(session: SessionDep, oid: int, datasource_id: int) -> None:
    exists = session.execute(
        select(CoreDatasource.id).where(CoreDatasource.id == datasource_id, CoreDatasource.oid == oid)
    ).first()
    if exists is None:
        raise HTTPException(status_code=403, detail="HEADLESS_DATASOURCE_NOT_FOUND")


def _ensure_relation_models(session: SessionDep, oid: int, payload: ModelRelationPayload) -> None:
    left_model = session.get(HeadlessModel, payload.left_model_id)
    right_model = session.get(HeadlessModel, payload.right_model_id)
    if left_model is None or right_model is None:
        raise HTTPException(status_code=404, detail="HEADLESS_MODEL_NOT_FOUND")
    if left_model.oid != oid or right_model.oid != oid:
        raise HTTPException(status_code=404, detail="HEADLESS_MODEL_NOT_FOUND")
    if left_model.status != 1 or right_model.status != 1:
        raise HTTPException(status_code=404, detail="HEADLESS_MODEL_NOT_FOUND")
    if left_model.domain_id != payload.domain_id or right_model.domain_id != payload.domain_id:
        raise HTTPException(status_code=400, detail="HEADLESS_MODEL_RELATION_DOMAIN_MISMATCH")


def _all(result):
    if hasattr(result, "scalars"):
        return result.scalars().all()
    if hasattr(result, "all"):
        return result.all()
    return list(result or [])


def _first_entity(result):
    # SQLAlchemy select(SQLModel) 可能返回 Row，统一通过 scalars() 解包为实体对象。
    if hasattr(result, "scalars"):
        return result.scalars().first()
    if hasattr(result, "first"):
        return result.first()
    return None


def _table_metas_from_persisted_tables(tables: list[CoreTable]) -> list[HeadlessTableMeta]:
    # 新建模型必须遵守数据源配置时勾选的表范围，避免实时读库绕过表白名单。
    return [
        HeadlessTableMeta(
            id=item.id,
            table_name=item.table_name,
            table_comment=_normalize_comment_text(item.custom_comment or item.table_comment),
            checked=item.checked,
        )
        for item in tables
        if item.checked
    ]


def _column_metas_from_persisted_fields(fields: list[CoreField]) -> list[HeadlessColumnMeta]:
    # 字段同样使用数据源已保存的中文注释，规避驱动实时读取注释时的编码不一致。
    return [
        HeadlessColumnMeta(
            id=item.id,
            field_name=item.field_name,
            field_type=item.field_type,
            field_comment=_normalize_comment_text(item.custom_comment or item.field_comment),
            field_index=item.field_index,
            checked=item.checked,
        )
        for item in fields
        if item.checked
    ]


def _normalize_comment_text(comment: str | None) -> str | None:
    if not comment:
        return comment
    raw_bytes = bytearray()
    try:
        # 兼容历史 MySQL 注释被按 latin1/CP1252 存入元数据表的情况。
        for char in comment:
            code_point = ord(char)
            if code_point <= 0xFF:
                raw_bytes.append(code_point)
            else:
                raw_bytes.extend(char.encode("cp1252"))
        return bytes(raw_bytes).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return comment


def _get_active(session: SessionDep, model_cls, oid: int, entity_id: int, detail: str):
    entity = session.get(model_cls, entity_id)
    if entity is None or entity.oid != oid or getattr(entity, "status", 1) != 1:
        raise HTTPException(status_code=404, detail=detail)
    return entity


def _assign_payload(entity, payload: dict) -> None:
    for key, value in payload.items():
        setattr(entity, key, value)


def _sync_model_storage(session: SessionDep, model: HeadlessModel) -> None:
    model.schema_version = (model.schema_version or 1) + 1
    model.last_schema_sync_at = datetime.now()
    session.exec(delete(HeadlessModelField).where(HeadlessModelField.oid == model.oid, HeadlessModelField.model_id == model.id))
    session.exec(delete(HeadlessModelMeasure).where(HeadlessModelMeasure.oid == model.oid, HeadlessModelMeasure.model_id == model.id))
    fields = model_fields_from_detail(model)
    for field in fields:
        field.model_id = model.id
        session.add(field)
    session.flush()
    for field in fields:
        if field.id is not None:
            aliases = build_aliases_for_asset("FIELD", field.id, field.oid, field.alias)
            _replace_aliases(session, field.oid, "FIELD", field.id, aliases)
    field_by_biz_name = {field.biz_name: field for field in fields}
    measures = model_measures_from_detail(model, field_by_biz_name)
    for measure in measures:
        measure.model_id = model.id
        session.add(measure)
    session.flush()
    for measure in measures:
        if measure.id is not None:
            aliases = build_aliases_for_asset("MEASURE", measure.id, measure.oid, measure.alias)
            _replace_aliases(session, measure.oid, "MEASURE", measure.id, aliases)
    _mark_domain_datasets_schema_changed(session, model.oid, model.domain_id)


def _validate_metric_storage_quality(session: SessionDep, metric: HeadlessMetric) -> None:
    if metric.quality_status == "INVALID":
        return

    fields = _all(
        session.exec(
            select(HeadlessModelField).where(
                HeadlessModelField.oid == metric.oid,
                HeadlessModelField.model_id == metric.model_id,
                HeadlessModelField.status == 1,
            )
        )
    )
    if fields and metric.fields:
        # 只在模型字段已经同步完成时做强校验，避免历史模型还未回填字段表时误判。
        known_fields = {
            value
            for field in fields
            for value in [field.biz_name, field.field_name, field.expr]
            if value
        }
        missing_fields = [field for field in metric.fields if field not in known_fields]
        if missing_fields:
            metric.quality_status = "INVALID"
            metric.quality_message = f"指标依赖字段不存在: {', '.join(missing_fields)}"
            return

    if metric.metric_refs:
        refs = _all(
            session.exec(
                select(HeadlessMetric.id).where(
                    HeadlessMetric.oid == metric.oid,
                    HeadlessMetric.model_id == metric.model_id,
                    HeadlessMetric.id.in_(metric.metric_refs),
                    HeadlessMetric.status == 1,
                )
            )
        )
        missing_refs = [ref for ref in metric.metric_refs if ref not in set(refs)]
        if missing_refs:
            metric.quality_status = "INVALID"
            metric.quality_message = f"派生指标引用不存在: {', '.join(str(item) for item in missing_refs)}"


def _mark_domain_datasets_schema_changed(session: SessionDep, oid: int, domain_id: int) -> None:
    datasets = _all(
        session.exec(
            select(HeadlessDataSet).where(
                HeadlessDataSet.oid == oid,
                HeadlessDataSet.domain_id == domain_id,
                HeadlessDataSet.status == 1,
            )
        )
    )
    for dataset in datasets:
        dataset.schema_version = (dataset.schema_version or 1) + 1
        session.add(dataset)
    _clear_domain_schema_indexes(session, oid, domain_id)


def _sync_dimension_values(session: SessionDep, dimension: HeadlessDimension) -> None:
    session.exec(
        delete(HeadlessDimensionValue).where(
            HeadlessDimensionValue.oid == dimension.oid,
            HeadlessDimensionValue.dimension_id == dimension.id,
        )
    )
    for value in dimension_values_from_maps(dimension):
        value.dimension_id = dimension.id
        value.model_id = dimension.model_id
        session.add(value)
    session.flush()
    values = _all(
        session.exec(
            select(HeadlessDimensionValue).where(
                HeadlessDimensionValue.oid == dimension.oid,
                HeadlessDimensionValue.dimension_id == dimension.id,
                HeadlessDimensionValue.status == 1,
            )
        )
    )
    for value in values:
        aliases = build_aliases_for_asset("VALUE", value.id, value.oid, [value.display_value, *(value.alias or [])], alias_type="VALUE")
        _replace_aliases(session, value.oid, "VALUE", value.id, aliases)
        _replace_relations(session, value.oid, "VALUE", value.id, build_dimension_value_relations(value))


def _sync_dataset_storage(session: SessionDep, dataset: HeadlessDataSet) -> None:
    session.exec(
        delete(HeadlessDataSetModelConfig).where(
            HeadlessDataSetModelConfig.oid == dataset.oid,
            HeadlessDataSetModelConfig.dataset_id == dataset.id,
        )
    )
    session.exec(
        delete(HeadlessDataSetAsset).where(
            HeadlessDataSetAsset.oid == dataset.oid,
            HeadlessDataSetAsset.dataset_id == dataset.id,
        )
    )
    for config in dataset_model_configs_from_detail(dataset):
        config.dataset_id = dataset.id
        session.add(config)
    for asset in dataset_assets_from_detail(dataset):
        asset.dataset_id = dataset.id
        session.add(asset)
    session.flush()
    assets = _all(
        session.exec(
            select(HeadlessDataSetAsset).where(
                HeadlessDataSetAsset.oid == dataset.oid,
                HeadlessDataSetAsset.dataset_id == dataset.id,
                HeadlessDataSetAsset.status == 1,
            )
        )
    )
    _replace_relations(session, dataset.oid, "DATASET", dataset.id, build_dataset_asset_relations(assets))


def _sync_metric_relation_storage(session: SessionDep, metric: HeadlessMetric) -> None:
    if metric.id is None:
        return
    aliases = build_aliases_for_asset("METRIC", metric.id, metric.oid, metric.alias)
    _replace_aliases(session, metric.oid, "METRIC", metric.id, aliases)
    _replace_relations(session, metric.oid, "METRIC", metric.id, build_metric_relations(metric))


def _sync_dimension_relation_storage(session: SessionDep, dimension: HeadlessDimension) -> None:
    if dimension.id is None:
        return
    aliases = build_aliases_for_asset("DIMENSION", dimension.id, dimension.oid, dimension.alias)
    _replace_aliases(session, dimension.oid, "DIMENSION", dimension.id, aliases)
    _replace_relations(session, dimension.oid, "DIMENSION", dimension.id, build_dimension_relations(dimension))


def _sync_term_relation_storage(session: SessionDep, term: HeadlessTerm) -> None:
    if term.id is None:
        return
    aliases = build_aliases_for_asset("TERM", term.id, term.oid, term.alias)
    _replace_aliases(session, term.oid, "TERM", term.id, aliases)
    _replace_relations(session, term.oid, "TERM", term.id, build_term_relations(term))


def _replace_aliases(
    session: SessionDep,
    oid: int,
    asset_type: str,
    asset_id: int,
    aliases: list[HeadlessAssetAlias],
) -> None:
    session.exec(
        delete(HeadlessAssetAlias).where(
            HeadlessAssetAlias.oid == oid,
            HeadlessAssetAlias.asset_type == asset_type,
            HeadlessAssetAlias.asset_id == asset_id,
        )
    )
    for alias in aliases:
        session.add(alias)


def _replace_relations(
    session: SessionDep,
    oid: int,
    source_type: str,
    source_id: int,
    relations: list[HeadlessAssetRelation],
) -> None:
    session.exec(
        delete(HeadlessAssetRelation).where(
            HeadlessAssetRelation.oid == oid,
            HeadlessAssetRelation.source_type == source_type,
            HeadlessAssetRelation.source_id == source_id,
        )
    )
    for relation in relations:
        session.add(relation)


def _clear_domain_schema_indexes(session: SessionDep, oid: int, domain_id: int) -> None:
    # 模型、指标、维度变更会影响数据集暴露范围，清理同主题域下的旧索引，后续由重建接口刷新。
    dataset_ids = _all(
        session.exec(select(HeadlessDataSet.id).where(HeadlessDataSet.oid == oid, HeadlessDataSet.domain_id == domain_id))
    )
    if dataset_ids:
        session.exec(
            delete(HeadlessSchemaIndex).where(
                HeadlessSchemaIndex.oid == oid,
                HeadlessSchemaIndex.dataset_id.in_(dataset_ids),
            )
        )
