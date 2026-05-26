from sqlalchemy import delete, select
from sqlmodel import Session

from apps.datasource.models.datasource import CoreField, CoreTable
from apps.semantic.crud.audit import list_asset_audits
from apps.semantic.crud.metric import approve_metric
from apps.semantic.models.semantic_model import (
    AggregationType,
    AssetOrigin,
    AssetStatus,
    AssetType,
    DimensionType,
    SemanticAssetAudit,
    SemanticDimension,
    SemanticMetric,
    SemanticType,
)
from apps.semantic.services.candidate_init import (
    infer_semantic_asset,
    initialize_candidates,
)
from common.core.db import engine


def make_table() -> CoreTable:
    return CoreTable(id=100, ds_id=10, checked=True, table_name="orders", table_comment="订单表", custom_comment="")


def make_field(name: str, field_type: str, comment: str = "") -> CoreField:
    return CoreField(
        id=200,
        ds_id=10,
        table_id=100,
        checked=True,
        field_name=name,
        field_type=field_type,
        field_comment=comment,
        custom_comment="",
        field_index=1,
    )


def assert_common_candidate(candidate, field_name: str):
    assert candidate.name == field_name
    assert candidate.display_name
    assert candidate.expr == field_name
    assert candidate.origin == AssetOrigin.FIELD_INIT.value
    assert candidate.status == AssetStatus.CANDIDATE.value


def test_infer_amount_metric_candidate():
    field = make_field("pay_amount", "decimal", "支付金额")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "pay_amount")
    assert candidate.asset_type == AssetType.METRIC.value
    assert candidate.default_agg == AggregationType.SUM.value
    assert candidate.data_format == "currency"


def test_infer_rate_metric_candidate():
    field = make_field("refund_rate", "double", "退款率")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "refund_rate")
    assert candidate.asset_type == AssetType.METRIC.value
    assert candidate.default_agg == AggregationType.AVG.value
    assert candidate.data_format == "percent"


def test_infer_id_dimension_candidate():
    field = make_field("user_id", "bigint", "用户 ID")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "user_id")
    assert candidate.asset_type == AssetType.DIMENSION.value
    assert candidate.dimension_type == DimensionType.ID.value


def test_infer_date_time_dimension_candidate():
    field = make_field("created_at", "timestamp", "创建时间")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "created_at")
    assert candidate.asset_type == AssetType.DIMENSION.value
    assert candidate.dimension_type == DimensionType.TIME.value
    assert candidate.semantic_type == SemanticType.DATETIME.value
    assert candidate.time_granularities == ["day", "week", "month", "quarter", "year"]


def test_infer_region_dimension_candidate():
    field = make_field("province_name", "varchar", "省份")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "province_name")
    assert candidate.asset_type == AssetType.DIMENSION.value
    assert candidate.dimension_type == DimensionType.GEO.value
    assert candidate.semantic_type == SemanticType.REGION.value


def test_infer_channel_dimension_candidate():
    field = make_field("order_channel", "varchar", "渠道")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "order_channel")
    assert candidate.asset_type == AssetType.DIMENSION.value
    assert candidate.dimension_type == DimensionType.CATEGORY.value
    assert candidate.semantic_type == SemanticType.CHANNEL.value


def test_infer_plain_text_dimension_candidate():
    field = make_field("product_category", "text", "商品类目")

    candidate = infer_semantic_asset(field, make_table())

    assert_common_candidate(candidate, "product_category")
    assert candidate.asset_type == AssetType.DIMENSION.value
    assert candidate.dimension_type == DimensionType.CATEGORY.value
    assert candidate.semantic_type == SemanticType.UNKNOWN.value


def cleanup_datasource_candidates(session: Session, datasource_id: int):
    metric_ids = session.execute(select(SemanticMetric.id).where(SemanticMetric.datasource_id == datasource_id)).scalars().all()
    dimension_ids = session.execute(select(SemanticDimension.id).where(SemanticDimension.datasource_id == datasource_id)).scalars().all()
    if metric_ids:
        session.execute(
            delete(SemanticAssetAudit).where(
                SemanticAssetAudit.asset_type == AssetType.METRIC.value,
                SemanticAssetAudit.asset_id.in_(metric_ids),
            )
        )
        session.execute(delete(SemanticMetric).where(SemanticMetric.id.in_(metric_ids)))
    if dimension_ids:
        session.execute(
            delete(SemanticAssetAudit).where(
                SemanticAssetAudit.asset_type == AssetType.DIMENSION.value,
                SemanticAssetAudit.asset_id.in_(dimension_ids),
            )
        )
        session.execute(delete(SemanticDimension).where(SemanticDimension.id.in_(dimension_ids)))
    field_ids = session.execute(select(CoreField.id).where(CoreField.ds_id == datasource_id)).scalars().all()
    if field_ids:
        session.execute(delete(CoreField).where(CoreField.id.in_(field_ids)))
    session.execute(delete(CoreTable).where(CoreTable.ds_id == datasource_id))
    session.commit()


def create_table_with_fields(
    session: Session,
    datasource_id: int,
    table_name: str,
    fields: list[tuple[str, str, str, bool]],
    checked: bool = True,
) -> CoreTable:
    table = CoreTable(
        ds_id=datasource_id,
        checked=checked,
        table_name=table_name,
        table_comment=table_name,
        custom_comment="",
    )
    session.add(table)
    session.flush()
    session.refresh(table)
    for index, (name, field_type, comment, field_checked) in enumerate(fields, start=1):
        session.add(
            CoreField(
                ds_id=datasource_id,
                table_id=table.id,
                checked=field_checked,
                field_name=name,
                field_type=field_type,
                field_comment=comment,
                custom_comment="",
                field_index=index,
            )
        )
    session.flush()
    return table


def test_initialize_candidates_dry_run_does_not_write_assets():
    datasource_id = 94100001
    oid = 9410

    with Session(engine) as session:
        cleanup_datasource_candidates(session, datasource_id)
        create_table_with_fields(
            session,
            datasource_id,
            "orders",
            [
                ("pay_amount", "decimal", "支付金额", True),
                ("order_date", "date", "下单日期", True),
                ("unchecked_city", "varchar", "城市", False),
            ],
        )
        session.commit()

        stats = initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=False, user_id=31, dry_run=True)
        session.commit()

        assert stats.created_metrics == 1
        assert stats.created_dimensions == 1
        assert session.execute(select(SemanticMetric).where(SemanticMetric.datasource_id == datasource_id)).first() is None
        assert session.execute(select(SemanticDimension).where(SemanticDimension.datasource_id == datasource_id)).first() is None

        cleanup_datasource_candidates(session, datasource_id)


def test_initialize_candidates_records_start_and_end_metrics(monkeypatch):
    datasource_id = 94100005
    oid = 9415
    recorded = []
    monkeypatch.setattr(
        "apps.semantic.services.candidate_init.record_semantic_metric",
        lambda name, **fields: recorded.append((name, fields)),
        raising=False,
    )

    with Session(engine) as session:
        cleanup_datasource_candidates(session, datasource_id)
        create_table_with_fields(
            session,
            datasource_id,
            "orders",
            [("pay_amount", "decimal", "支付金额", True)],
        )
        session.commit()

        initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=False, user_id=31, dry_run=True)

        assert [item[0] for item in recorded] == [
            "semantic_candidate_init_total",
            "semantic_candidate_init_total",
        ]
        assert recorded[0][1]["phase"] == "start"
        assert recorded[0][1]["datasource_id"] == datasource_id
        assert recorded[1][1]["phase"] == "end"
        assert recorded[1][1]["created_metrics"] == 1
        assert recorded[1][1]["created_dimensions"] == 0
        assert recorded[1][1]["skipped"] == 0

        cleanup_datasource_candidates(session, datasource_id)


def test_initialize_candidates_is_idempotent_and_overwrites_candidate():
    datasource_id = 94100002
    oid = 9411

    with Session(engine) as session:
        cleanup_datasource_candidates(session, datasource_id)
        create_table_with_fields(session, datasource_id, "orders", [("pay_amount", "decimal", "支付金额", True)])
        session.commit()

        first = initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=False, user_id=31, dry_run=False)
        session.commit()
        second = initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=False, user_id=31, dry_run=False)
        session.commit()
        field = session.execute(select(CoreField).where(CoreField.ds_id == datasource_id, CoreField.field_name == "pay_amount")).scalar_one()
        field.field_comment = "支付金额新口径"
        session.add(field)
        overwrite = initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=True, user_id=32, dry_run=False)
        session.commit()
        metric = session.execute(select(SemanticMetric).where(SemanticMetric.datasource_id == datasource_id)).scalar_one()
        audits = list_asset_audits(session, AssetType.METRIC.value, metric.id)

        assert first.created_metrics == 1
        assert second.skipped == 1
        assert overwrite.updated_metrics == 1
        assert metric.display_name == "支付金额新口径"
        assert [audit.action for audit in audits] == ["UPDATE_CANDIDATE", "CREATE"]

        cleanup_datasource_candidates(session, datasource_id)


def test_initialize_candidates_skips_duplicate_candidate_names_across_tables():
    datasource_id = 94100006
    oid = 9416

    with Session(engine) as session:
        cleanup_datasource_candidates(session, datasource_id)
        create_table_with_fields(session, datasource_id, "orders", [("channel", "varchar", "下单渠道", True)])
        create_table_with_fields(session, datasource_id, "traffic_daily", [("channel", "varchar", "流量渠道", True)])
        session.commit()

        stats = initialize_candidates(session, oid, datasource_id, table_ids=[], overwrite_candidate=False, user_id=31, dry_run=False)
        session.commit()
        dimensions = session.execute(
            select(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.datasource_id == datasource_id,
                SemanticDimension.name == "channel",
            )
        ).scalars().all()

        assert stats.created_dimensions == 1
        assert stats.skipped == 1
        assert len(dimensions) == 1

        cleanup_datasource_candidates(session, datasource_id)


def test_initialize_candidates_skips_approved_assets_and_honors_table_ids():
    datasource_id = 94100003
    oid = 9412

    with Session(engine) as session:
        cleanup_datasource_candidates(session, datasource_id)
        target_table = create_table_with_fields(session, datasource_id, "orders", [("pay_amount", "decimal", "支付金额", True)])
        other_table = create_table_with_fields(session, datasource_id, "users", [("city_name", "varchar", "城市", True)])
        session.commit()

        first = initialize_candidates(session, oid, datasource_id, table_ids=[target_table.id], overwrite_candidate=False, user_id=31, dry_run=False)
        session.commit()
        metric = session.execute(select(SemanticMetric).where(SemanticMetric.datasource_id == datasource_id)).scalar_one()
        approve_metric(session, oid=oid, metric_id=metric.id, user_id=33)
        session.commit()
        field = session.execute(select(CoreField).where(CoreField.table_id == target_table.id)).scalar_one()
        field.field_comment = "不应覆盖"
        session.add(field)
        overwrite = initialize_candidates(
            session,
            oid,
            datasource_id,
            table_ids=[target_table.id, other_table.id],
            overwrite_candidate=True,
            user_id=34,
            dry_run=False,
        )
        session.commit()
        session.refresh(metric)
        dimension = session.execute(select(SemanticDimension).where(SemanticDimension.datasource_id == datasource_id)).scalar_one()

        assert first.created_metrics == 1
        assert overwrite.skipped == 1
        assert overwrite.created_dimensions == 1
        assert metric.display_name == "支付金额"
        assert metric.status == AssetStatus.APPROVED.value
        assert dimension.name == "city_name"

        cleanup_datasource_candidates(session, datasource_id)
