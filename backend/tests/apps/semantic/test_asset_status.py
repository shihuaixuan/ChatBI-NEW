from datetime import datetime

import pytest
from sqlalchemy import delete, select
from sqlmodel import Session

import apps.semantic.crud.dimension as dimension_crud
import apps.semantic.crud.metric as metric_crud
from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.crud.audit import create_audit, list_asset_audits
from apps.semantic.crud.dimension import (
    approve_dimension,
    create_dimension,
    disable_dimension,
    get_dimension,
    page_dimensions,
    update_dimension,
)
from apps.semantic.crud.metric import (
    approve_metric,
    create_metric,
    disable_metric,
    get_metric,
    page_metrics,
    update_metric,
)
from apps.semantic.models.semantic_model import (
    AssetStatus,
    AssetType,
    DimensionType,
    SemanticAssetAudit,
    SemanticDimension,
    SemanticMetric,
    SemanticType,
)
from apps.semantic.models.semantic_schema import (
    DimensionCreate,
    DimensionUpdate,
    MetricCreate,
    MetricUpdate,
)
from common.core.db import engine


@pytest.fixture(autouse=True)
def disable_background_embedding_rebuild(monkeypatch):
    monkeypatch.setattr(metric_crud, "submit_metric_embedding_rebuild", lambda _metric_id: None)
    monkeypatch.setattr(dimension_crud, "submit_dimension_embedding_rebuild", lambda _dimension_id: None)


def test_create_audit_saves_sanitized_snapshot():
    asset_id = 93010001

    with Session(engine) as session:
        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_id == asset_id))
        session.commit()

        audit = create_audit(
            session=session,
            asset_type=AssetType.METRIC.value,
            asset_id=asset_id,
            action="CREATE",
            before={"name": "old", "password": "secret"},
            after={
                "name": "sales_amount",
                "configuration": "postgresql://user:password@localhost/db",
                "nested": {"token": "secret-token", "display_name": "销售额"},
            },
            user_id=42,
        )
        session.commit()
        session.refresh(audit)

        assert audit.id is not None
        assert audit.asset_type == AssetType.METRIC.value
        assert audit.action == "CREATE"
        assert audit.before == {"name": "old"}
        assert audit.after == {"name": "sales_amount", "nested": {"display_name": "销售额"}}
        assert audit.created_by == 42
        assert isinstance(audit.created_at, datetime)

        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_id == asset_id))
        session.commit()


def test_list_asset_audits_returns_records_in_descending_created_order():
    asset_id = 93010002

    with Session(engine) as session:
        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_id == asset_id))
        session.commit()

        create_audit(session, AssetType.DIMENSION.value, asset_id, "CREATE", None, {"name": "region"}, 7)
        create_audit(session, AssetType.DIMENSION.value, asset_id, "UPDATE", {"name": "region"}, {"name": "province"}, 7)
        create_audit(session, AssetType.DIMENSION.value, asset_id, "APPROVE", {"status": "CANDIDATE"}, {"status": "APPROVED"}, 7)
        create_audit(session, AssetType.DIMENSION.value, asset_id, "DISABLE", {"status": "APPROVED"}, {"status": "DISABLED"}, 7)
        session.commit()

        audits = list_asset_audits(session, AssetType.DIMENSION.value, asset_id)

        assert [audit.action for audit in audits] == ["DISABLE", "APPROVE", "UPDATE", "CREATE"]

        session.execute(delete(SemanticAssetAudit).where(SemanticAssetAudit.asset_id == asset_id))
        session.commit()


def cleanup_metric(session: Session, oid: int, datasource_id: int, name_prefix: str):
    metric_ids = session.execute(
        select(SemanticMetric.id).where(
            SemanticMetric.oid == oid,
            SemanticMetric.datasource_id == datasource_id,
            SemanticMetric.name.ilike(f"{name_prefix}%"),
        )
    ).scalars().all()
    if metric_ids:
        session.execute(
            delete(SemanticAssetAudit).where(
                SemanticAssetAudit.asset_type == AssetType.METRIC.value,
                SemanticAssetAudit.asset_id.in_(metric_ids),
            )
        )
        session.execute(delete(SemanticMetric).where(SemanticMetric.id.in_(metric_ids)))
    session.commit()


def cleanup_dimension(session: Session, oid: int, datasource_id: int, name_prefix: str):
    dimension_ids = session.execute(
        select(SemanticDimension.id).where(
            SemanticDimension.oid == oid,
            SemanticDimension.datasource_id == datasource_id,
            SemanticDimension.name.ilike(f"{name_prefix}%"),
        )
    ).scalars().all()
    if dimension_ids:
        session.execute(
            delete(SemanticAssetAudit).where(
                SemanticAssetAudit.asset_type == AssetType.DIMENSION.value,
                SemanticAssetAudit.asset_id.in_(dimension_ids),
            )
        )
        session.execute(delete(SemanticDimension).where(SemanticDimension.id.in_(dimension_ids)))
    session.commit()


def create_source_table(session: Session, datasource_id: int) -> CoreTable:
    table = CoreTable(
        ds_id=datasource_id,
        checked=True,
        table_name="orders",
        table_comment="订单表",
        custom_comment="",
    )
    session.add(table)
    session.flush()
    session.refresh(table)
    for index, field_name in enumerate(["pay_amount", "order_date"], start=1):
        session.add(
            CoreField(
                ds_id=datasource_id,
                table_id=table.id,
                checked=True,
                field_name=field_name,
                field_type="decimal" if field_name == "pay_amount" else "date",
                field_comment=field_name,
                custom_comment="",
                field_index=index,
            )
        )
    session.flush()
    return table


def cleanup_source_table(session: Session, datasource_id: int):
    session.execute(delete(CoreField).where(CoreField.ds_id == datasource_id))
    session.execute(delete(CoreTable).where(CoreTable.ds_id == datasource_id))
    session.commit()


def test_metric_crud_creates_updates_pages_and_writes_audit():
    oid = 9302
    datasource_id = 93020001
    name_prefix = "metric_crud_sales"

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)

        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_amount",
                display_name="销售额",
                aliases=["GMV", "收入"],
                expr="pay_amount",
            ),
            user_id=11,
        )
        session.commit()

        updated = update_metric(
            session,
            oid=oid,
            metric_id=metric.id,
            payload=MetricUpdate(display_name="支付销售额", aliases=["GMV", "业绩"], expr="paid_amount"),
            user_id=12,
        )
        session.commit()

        current_page, page_size, total_count, metrics = page_metrics(
            session,
            oid=oid,
            datasource_id=datasource_id,
            page=1,
            size=10,
            keyword="业绩",
            status=None,
            table_id=None,
            owner_id=None,
        )
        audits = list_asset_audits(session, AssetType.METRIC.value, metric.id)

        assert get_metric(session, oid, metric.id).display_name == "支付销售额"
        assert updated.expr == "paid_amount"
        assert current_page == 1
        assert page_size == 10
        assert total_count == 1
        assert [item.id for item in metrics] == [metric.id]
        assert [audit.action for audit in audits] == ["UPDATE", "CREATE"]

        cleanup_metric(session, oid, datasource_id, name_prefix)


def test_metric_create_rejects_duplicate_name_in_same_datasource():
    oid = 9303
    datasource_id = 93030001
    name_prefix = "metric_duplicate"

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)
        payload = MetricCreate(
            datasource_id=datasource_id,
            name=f"{name_prefix}_amount",
            display_name="销售额",
            expr="pay_amount",
        )
        create_metric(session, oid=oid, payload=payload, user_id=11)
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_ASSET_DUPLICATED"):
            create_metric(session, oid=oid, payload=payload, user_id=11)

        cleanup_metric(session, oid, datasource_id, name_prefix)


def test_metric_approve_and_disable_status_flow_writes_audit():
    oid = 9304
    datasource_id = 93040001
    name_prefix = "metric_status"

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)
        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_amount",
                display_name="销售额",
                expr="pay_amount",
            ),
            user_id=11,
        )
        session.commit()

        approved = approve_metric(session, oid=oid, metric_id=metric.id, user_id=12)
        approved_status = approved.status
        session.commit()
        disabled = disable_metric(session, oid=oid, metric_id=metric.id, reason="口径废弃", user_id=13)
        disabled_status = disabled.status
        session.commit()
        disabled_again = disable_metric(session, oid=oid, metric_id=metric.id, reason="重复禁用", user_id=13)
        disabled_again_status = disabled_again.status
        session.commit()
        reapproved = approve_metric(session, oid=oid, metric_id=metric.id, user_id=14)
        reapproved_status = reapproved.status
        session.commit()
        audits = list_asset_audits(session, AssetType.METRIC.value, metric.id)

        assert approved_status == AssetStatus.APPROVED.value
        assert disabled_status == AssetStatus.DISABLED.value
        assert disabled_again_status == AssetStatus.DISABLED.value
        assert reapproved_status == AssetStatus.APPROVED.value
        assert [audit.action for audit in audits] == ["APPROVE", "DISABLE", "APPROVE", "CREATE"]

        cleanup_metric(session, oid, datasource_id, name_prefix)


def test_metric_update_triggers_embedding_rebuild_for_approved_asset(monkeypatch):
    oid = 9305
    datasource_id = 93050001
    name_prefix = "metric_embedding_hook"
    submitted_metric_ids = []

    monkeypatch.setattr(metric_crud, "submit_metric_embedding_rebuild", submitted_metric_ids.append)

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)
        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_amount",
                display_name="销售额",
                expr="pay_amount",
            ),
            user_id=11,
        )
        approve_metric(session, oid=oid, metric_id=metric.id, user_id=12)
        session.commit()
        submitted_metric_ids.clear()

        update_metric(
            session,
            oid=oid,
            metric_id=metric.id,
            payload=MetricUpdate(display_name="已审核销售额"),
            user_id=13,
        )
        session.commit()

        assert submitted_metric_ids == [metric.id]

        cleanup_metric(session, oid, datasource_id, name_prefix)


def test_metric_approve_submits_embedding_rebuild_after_commit(monkeypatch):
    oid = 9311
    datasource_id = 93110001
    name_prefix = "metric_approve_embedding_after_commit"
    submitted_metric_ids = []

    monkeypatch.setattr(metric_crud, "submit_metric_embedding_rebuild", submitted_metric_ids.append)

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)
        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_amount",
                display_name="销售额",
                expr="pay_amount",
            ),
            user_id=11,
        )
        session.commit()

        approve_metric(session, oid=oid, metric_id=metric.id, user_id=12)

        assert submitted_metric_ids == []

        session.commit()

        assert submitted_metric_ids == [metric.id]

        cleanup_metric(session, oid, datasource_id, name_prefix)


def test_metric_approve_validation_failure_keeps_candidate_status(monkeypatch):
    oid = 9309
    datasource_id = 93090001
    name_prefix = "metric_invalid_expr"
    recorded = []
    monkeypatch.setattr(
        metric_crud,
        "record_semantic_metric",
        lambda name, **fields: recorded.append((name, fields)),
        raising=False,
    )

    with Session(engine) as session:
        cleanup_metric(session, oid, datasource_id, name_prefix)
        cleanup_source_table(session, datasource_id)
        table = create_source_table(session, datasource_id)
        metric = create_metric(
            session,
            oid=oid,
            payload=MetricCreate(
                datasource_id=datasource_id,
                table_id=table.id,
                name=f"{name_prefix}_amount",
                display_name="错误销售额",
                expr="unknown_amount",
            ),
            user_id=11,
        )
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_EXPR_INVALID"):
            approve_metric(session, oid=oid, metric_id=metric.id, user_id=12)
        session.rollback()
        current = get_metric(session, oid, metric.id)

        assert current.status == AssetStatus.CANDIDATE.value
        assert recorded[0][0] == "semantic_expr_validate_failed_total"
        assert recorded[0][1]["asset_type"] == AssetType.METRIC.value
        assert recorded[0][1]["asset_id"] == metric.id
        assert recorded[0][1]["expr"] == "unknown_amount"
        assert recorded[0][1]["error"]

        cleanup_metric(session, oid, datasource_id, name_prefix)
        cleanup_source_table(session, datasource_id)


def test_dimension_crud_creates_updates_pages_and_writes_audit():
    oid = 9306
    datasource_id = 93060001
    name_prefix = "dimension_crud_region"

    with Session(engine) as session:
        cleanup_dimension(session, oid, datasource_id, name_prefix)

        dimension = create_dimension(
            session,
            oid=oid,
            payload=DimensionCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_name",
                display_name="区域",
                aliases=["省份", "城市"],
                expr="region_name",
                dimension_type=DimensionType.GEO.value,
                semantic_type=SemanticType.REGION.value,
            ),
            user_id=21,
        )
        session.commit()

        updated = update_dimension(
            session,
            oid=oid,
            dimension_id=dimension.id,
            payload=DimensionUpdate(display_name="销售区域", aliases=["地区", "大区"], expr="sales_region"),
            user_id=22,
        )
        session.commit()

        current_page, page_size, total_count, dimensions = page_dimensions(
            session,
            oid=oid,
            datasource_id=datasource_id,
            page=1,
            size=10,
            keyword="大区",
            status=None,
            table_id=None,
            owner_id=None,
        )
        audits = list_asset_audits(session, AssetType.DIMENSION.value, dimension.id)

        assert get_dimension(session, oid, dimension.id).display_name == "销售区域"
        assert updated.expr == "sales_region"
        assert current_page == 1
        assert page_size == 10
        assert total_count == 1
        assert [item.id for item in dimensions] == [dimension.id]
        assert [audit.action for audit in audits] == ["UPDATE", "CREATE"]

        cleanup_dimension(session, oid, datasource_id, name_prefix)


def test_dimension_create_rejects_duplicate_name_and_invalid_time_semantic_type():
    oid = 9307
    datasource_id = 93070001
    name_prefix = "dimension_duplicate"

    with Session(engine) as session:
        cleanup_dimension(session, oid, datasource_id, name_prefix)
        payload = DimensionCreate(
            datasource_id=datasource_id,
            name=f"{name_prefix}_region",
            display_name="区域",
            expr="region",
        )
        create_dimension(session, oid=oid, payload=payload, user_id=21)
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_ASSET_DUPLICATED"):
            create_dimension(session, oid=oid, payload=payload, user_id=21)

        with pytest.raises(ValueError, match="SEMANTIC_TIME_DIMENSION_TYPE_INVALID"):
            create_dimension(
                session,
                oid=oid,
                payload=DimensionCreate(
                    datasource_id=datasource_id,
                    name=f"{name_prefix}_bad_time",
                    display_name="坏时间维度",
                    expr="created_at",
                    dimension_type=DimensionType.TIME.value,
                    semantic_type=SemanticType.UNKNOWN.value,
                ),
                user_id=21,
            )

        cleanup_dimension(session, oid, datasource_id, name_prefix)


def test_dimension_approve_and_disable_status_flow_writes_audit():
    oid = 9308
    datasource_id = 93080001
    name_prefix = "dimension_status"

    with Session(engine) as session:
        cleanup_dimension(session, oid, datasource_id, name_prefix)
        dimension = create_dimension(
            session,
            oid=oid,
            payload=DimensionCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_date",
                display_name="下单日期",
                expr="order_date",
                dimension_type=DimensionType.TIME.value,
                semantic_type=SemanticType.DATE.value,
                time_granularities=["day", "month", "year"],
            ),
            user_id=21,
        )
        session.commit()

        approved = approve_dimension(session, oid=oid, dimension_id=dimension.id, user_id=22)
        approved_status = approved.status
        session.commit()
        disabled = disable_dimension(session, oid=oid, dimension_id=dimension.id, reason="口径废弃", user_id=23)
        disabled_status = disabled.status
        session.commit()
        _, _, approved_count_while_disabled, approved_dimensions_while_disabled = page_dimensions(
            session,
            oid=oid,
            datasource_id=datasource_id,
            page=1,
            size=10,
            keyword=None,
            status=AssetStatus.APPROVED.value,
            table_id=None,
            owner_id=None,
        )
        disabled_again = disable_dimension(session, oid=oid, dimension_id=dimension.id, reason="重复禁用", user_id=23)
        disabled_again_status = disabled_again.status
        session.commit()
        reapproved = approve_dimension(session, oid=oid, dimension_id=dimension.id, user_id=24)
        reapproved_status = reapproved.status
        session.commit()
        update_dimension(
            session,
            oid=oid,
            dimension_id=dimension.id,
            payload=DimensionUpdate(display_name="已审核下单日期"),
            user_id=25,
        )
        session.commit()
        audits = list_asset_audits(session, AssetType.DIMENSION.value, dimension.id)

        assert approved_status == AssetStatus.APPROVED.value
        assert disabled_status == AssetStatus.DISABLED.value
        assert approved_count_while_disabled == 0
        assert approved_dimensions_while_disabled == []
        assert disabled_again_status == AssetStatus.DISABLED.value
        assert reapproved_status == AssetStatus.APPROVED.value
        assert [audit.action for audit in audits] == ["UPDATE", "APPROVE", "DISABLE", "APPROVE", "CREATE"]
        assert reapproved.time_granularities == ["day", "month", "year"]

        cleanup_dimension(session, oid, datasource_id, name_prefix)


def test_dimension_approve_validation_failure_keeps_candidate_status(monkeypatch):
    oid = 9310
    datasource_id = 93100001
    name_prefix = "dimension_invalid_expr"
    recorded = []
    monkeypatch.setattr(
        dimension_crud,
        "record_semantic_metric",
        lambda name, **fields: recorded.append((name, fields)),
        raising=False,
    )

    with Session(engine) as session:
        cleanup_dimension(session, oid, datasource_id, name_prefix)
        cleanup_source_table(session, datasource_id)
        table = create_source_table(session, datasource_id)
        dimension = create_dimension(
            session,
            oid=oid,
            payload=DimensionCreate(
                datasource_id=datasource_id,
                table_id=table.id,
                name=f"{name_prefix}_date",
                display_name="错误日期",
                expr="missing_date",
            ),
            user_id=21,
        )
        session.commit()

        with pytest.raises(ValueError, match="SEMANTIC_EXPR_INVALID"):
            approve_dimension(session, oid=oid, dimension_id=dimension.id, user_id=22)
        session.rollback()
        current = get_dimension(session, oid, dimension.id)

        assert current.status == AssetStatus.CANDIDATE.value
        assert recorded[0][0] == "semantic_expr_validate_failed_total"
        assert recorded[0][1]["asset_type"] == AssetType.DIMENSION.value
        assert recorded[0][1]["asset_id"] == dimension.id
        assert recorded[0][1]["expr"] == "missing_date"
        assert recorded[0][1]["error"]

        cleanup_dimension(session, oid, datasource_id, name_prefix)
        cleanup_source_table(session, datasource_id)


def test_dimension_approve_submits_embedding_rebuild_after_commit(monkeypatch):
    oid = 9312
    datasource_id = 93120001
    name_prefix = "dimension_approve_embedding_after_commit"
    submitted_dimension_ids = []

    monkeypatch.setattr(dimension_crud, "submit_dimension_embedding_rebuild", submitted_dimension_ids.append)

    with Session(engine) as session:
        cleanup_dimension(session, oid, datasource_id, name_prefix)
        dimension = create_dimension(
            session,
            oid=oid,
            payload=DimensionCreate(
                datasource_id=datasource_id,
                name=f"{name_prefix}_date",
                display_name="下单日期",
                expr="order_date",
                dimension_type=DimensionType.TIME.value,
                semantic_type=SemanticType.DATE.value,
            ),
            user_id=21,
        )
        session.commit()

        approve_dimension(session, oid=oid, dimension_id=dimension.id, user_id=22)

        assert submitted_dimension_ids == []

        session.commit()

        assert submitted_dimension_ids == [dimension.id]

        cleanup_dimension(session, oid, datasource_id, name_prefix)
