from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import delete, select
from sqlmodel import Session

import apps.semantic.crud.dimension as dimension_crud
import apps.semantic.crud.metric as metric_crud
from apps.chat.models.chat_model import Chat, ChatRecord
from apps.datasource.models.datasource import CoreDatasource, CoreField, CoreTable
from apps.semantic.api import semantic
from apps.semantic.api.semantic import _ensure_datasource_access, _semantic_error
from apps.semantic.crud.usage import save_chat_asset_usage
from apps.semantic.models.semantic_model import (
    AssetStatus,
    AssetType,
    ChatRecordSemanticAsset,
    SemanticAssetAudit,
    SemanticDimension,
    SemanticMetric,
)
from apps.semantic.models.semantic_schema import (
    DimensionCreate,
    DisableAssetRequest,
    InitializeSemanticRequest,
    MetricCreate,
    MetricUpdate,
    SemanticAssetMatch,
    ValidateExpressionRequest,
)
from common.core.db import engine


def test_semantic_routes_are_registered_in_openapi():
    app = FastAPI()
    app.include_router(semantic.router)

    paths = app.openapi()["paths"]

    expected_routes = {
        "/semantic/metrics/page/{page}/{size}",
        "/semantic/metrics",
        "/semantic/metrics/{id}",
        "/semantic/metrics/{id}/approve",
        "/semantic/metrics/{id}/disable",
        "/semantic/metrics/{id}/embedding",
        "/semantic/dimensions/page/{page}/{size}",
        "/semantic/dimensions",
        "/semantic/dimensions/{id}",
        "/semantic/dimensions/{id}/approve",
        "/semantic/dimensions/{id}/disable",
        "/semantic/dimensions/{id}/embedding",
        "/semantic/datasources/{datasource_id}/initialize",
        "/semantic/datasources/{datasource_id}/validate",
        "/semantic/chat-records/{record_id}/assets",
    }
    assert expected_routes.issubset(paths.keys())


def test_apps_api_includes_semantic_router():
    api_py = Path(__file__).parents[3] / "apps" / "api.py"

    source = api_py.read_text()

    assert "from apps.semantic.api import semantic" in source
    assert "include_router(semantic.router)" in source


def test_semantic_routes_require_current_user_dependency():
    route_params = {
        route.path: signature(route.endpoint).parameters
        for route in semantic.router.routes
        if isinstance(route, APIRoute)
    }

    assert route_params
    assert all("current_user" in params for params in route_params.values())


def test_datasource_access_rejects_datasource_outside_current_oid():
    with Session(engine) as session:
        with pytest.raises(Exception) as exc_info:
            _ensure_datasource_access(session, oid=9313, datasource_id=93130001)

    assert exc_info.value.status_code == 403
    assert "SEMANTIC_PERMISSION_DENIED" in exc_info.value.detail


def test_semantic_error_maps_business_codes_to_http_status():
    expected_status = {
        "SEMANTIC_DATASOURCE_NOT_FOUND": 404,
        "SEMANTIC_ASSET_NOT_FOUND": 404,
        "SEMANTIC_ASSET_DUPLICATED": 400,
        "SEMANTIC_STATUS_INVALID": 400,
        "SEMANTIC_EXPR_INVALID": 400,
        "SEMANTIC_SOURCE_FIELD_MISSING": 400,
        "SEMANTIC_EMBEDDING_FAILED": 500,
        "SEMANTIC_PERMISSION_DENIED": 403,
    }

    for code, status_code in expected_status.items():
        error = _semantic_error(ValueError(f"{code}: readable reason"))

        assert error.status_code == status_code
        assert code in error.detail
        assert "readable reason" in error.detail


def test_semantic_error_detail_does_not_expose_sensitive_values():
    error = _semantic_error(
        ValueError(
            "SEMANTIC_EXPR_INVALID: postgresql://user:secret@localhost/db "
            "password=secret token=secret configuration=secret"
        )
    )

    assert error.status_code == 400
    assert "secret" not in error.detail
    assert "postgresql://user" not in error.detail
    assert "SEMANTIC_EXPR_INVALID" in error.detail


def _cleanup_api_fixture(session: Session, oid: int, datasource_id: int | None = None) -> None:
    datasource_ids = set(
        session.execute(select(CoreDatasource.id).where(CoreDatasource.oid == oid, CoreDatasource.name == "semantic-api-test"))
        .scalars()
        .all()
    )
    if datasource_id is not None:
        datasource_ids.add(datasource_id)
    metric_ids = session.execute(
        select(SemanticMetric.id).where(SemanticMetric.oid == oid, SemanticMetric.datasource_id.in_(datasource_ids))
    ).scalars().all()
    dimension_ids = session.execute(
        select(SemanticDimension.id).where(SemanticDimension.oid == oid, SemanticDimension.datasource_id.in_(datasource_ids))
    ).scalars().all()
    if metric_ids:
        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_type == AssetType.METRIC.value, SemanticAssetAudit.asset_id.in_(metric_ids)))
        session.execute(delete(SemanticMetric).where(SemanticMetric.id.in_(metric_ids)))
    if dimension_ids:
        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_type == AssetType.DIMENSION.value, SemanticAssetAudit.asset_id.in_(dimension_ids)))
        session.execute(delete(SemanticDimension).where(SemanticDimension.id.in_(dimension_ids)))
    record_ids = session.execute(select(ChatRecord.id).join(Chat, Chat.id == ChatRecord.chat_id).where(Chat.oid == oid)).scalars().all()
    if record_ids:
        session.execute(delete(ChatRecordSemanticAsset).where(ChatRecordSemanticAsset.record_id.in_(record_ids)))
        session.execute(delete(ChatRecord).where(ChatRecord.id.in_(record_ids)))
    session.execute(delete(Chat).where(Chat.oid == oid))
    if datasource_ids:
        session.execute(delete(CoreField).where(CoreField.ds_id.in_(datasource_ids)))
        session.execute(delete(CoreTable).where(CoreTable.ds_id.in_(datasource_ids)))
        session.execute(delete(CoreDatasource).where(CoreDatasource.id.in_(datasource_ids)))
    session.commit()


def _create_api_fixture(session: Session, oid: int) -> tuple[CoreDatasource, CoreTable]:
    datasource = CoreDatasource(
        oid=oid,
        name="semantic-api-test",
        type="postgresql",
        type_name="PostgreSQL",
        configuration="{}",
        create_by=1,
        status="ACTIVE",
        recommended_config=1,
    )
    session.add(datasource)
    session.flush()
    session.refresh(datasource)
    table = CoreTable(ds_id=datasource.id, checked=True, table_name="orders", table_comment="orders", custom_comment="")
    session.add(table)
    session.flush()
    session.refresh(table)
    session.add(
        CoreField(
            ds_id=datasource.id,
            table_id=table.id,
            checked=True,
            field_name="pay_amount",
            field_type="decimal",
            field_comment="支付金额",
            custom_comment="",
            field_index=1,
        )
    )
    session.add(
        CoreField(
            ds_id=datasource.id,
            table_id=table.id,
            checked=True,
            field_name="order_date",
            field_type="date",
            field_comment="下单日期",
            custom_comment="",
            field_index=2,
        )
    )
    session.commit()
    return datasource, table


@pytest.mark.anyio
async def test_semantic_api_handlers_cover_crud_initialize_validate_and_usage(monkeypatch):
    oid = 9320
    user = SimpleNamespace(id=501, oid=oid)

    monkeypatch.setattr(metric_crud, "submit_metric_embedding_rebuild", lambda _metric_id: None)
    monkeypatch.setattr(dimension_crud, "submit_dimension_embedding_rebuild", lambda _dimension_id: None)

    with Session(engine) as session:
        _cleanup_api_fixture(session, oid)
        datasource, table = _create_api_fixture(session, oid)
        datasource_id = datasource.id

        metric = await semantic.metric_create(
            session,
            user,
            MetricCreate(datasource_id=datasource_id, name="api_sales_amount", display_name="销售额", expr="pay_amount"),
        )
        assert metric.id is not None

        with pytest.raises(Exception) as duplicate_exc:
            await semantic.metric_create(
                session,
                user,
                MetricCreate(datasource_id=datasource_id, name="api_sales_amount", display_name="销售额", expr="pay_amount"),
            )
        assert duplicate_exc.value.status_code == 400

        updated = await semantic.metric_update(session, user, metric.id, MetricUpdate(display_name="支付销售额"))
        assert updated.display_name == "支付销售额"

        approved_metric = await semantic.metric_approve(session, user, metric.id)
        assert approved_metric["status"] == AssetStatus.APPROVED.value

        disabled_metric = await semantic.metric_disable(session, user, metric.id, DisableAssetRequest(reason="api test"))
        assert disabled_metric.status == AssetStatus.DISABLED.value

        dimension = await semantic.dimension_create(
            session,
            user,
            DimensionCreate(datasource_id=datasource_id, name="api_order_date", display_name="下单日期", expr="order_date"),
        )
        approved_dimension = await semantic.dimension_approve(session, user, dimension.id)
        assert approved_dimension["status"] == AssetStatus.APPROVED.value

        initialized = await semantic.datasource_initialize(
            session,
            user,
            datasource_id,
            InitializeSemanticRequest(dry_run=True),
        )
        assert initialized.created_metrics >= 0

        valid_expr = await semantic.datasource_validate(
            session,
            user,
            datasource_id,
            ValidateExpressionRequest(table_id=table.id, expr="pay_amount"),
        )
        invalid_expr = await semantic.datasource_validate(
            session,
            user,
            datasource_id,
            ValidateExpressionRequest(table_id=table.id, expr="missing_amount"),
        )
        assert valid_expr.valid is True
        assert invalid_expr.valid is False

        chat = Chat(oid=oid, create_by=user.id, brief="api usage", chat_type="chat", datasource=datasource_id, engine_type="PostgreSQL")
        session.add(chat)
        session.flush()
        session.refresh(chat)
        record = ChatRecord(chat_id=chat.id, create_by=user.id, datasource=datasource_id, engine_type="PostgreSQL", question="销售额")
        session.add(record)
        session.flush()
        session.refresh(record)
        save_chat_asset_usage(
            session,
            record.id,
            [
                SemanticAssetMatch(
                    asset_type=AssetType.METRIC.value,
                    asset_id=metric.id,
                    name=metric.name,
                    display_name=metric.display_name,
                    score=0.9,
                    snapshot={"expr": metric.expr},
                )
            ],
        )
        session.commit()

        usage = await semantic.chat_record_assets(session, user, record.id)
        assert usage.record_id == record.id
        assert usage.assets[0].asset_id == metric.id

        _cleanup_api_fixture(session, oid, datasource_id)
