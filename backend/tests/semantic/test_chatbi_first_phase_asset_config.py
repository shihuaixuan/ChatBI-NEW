from scripts.configure_chatbi_first_phase_assets import build_asset_plan


def test_asset_plan_uses_one_default_business_time_per_model():
    plan = build_asset_plan()

    assert plan["fct_stall_order_daily"]["default_time"] == "stat_date"
    assert plan["snap_unshipped_order"]["default_time"] == "snapshot_date"
    assert plan["snap_product_inventory"]["default_time"] == "snapshot_date"
    assert plan["fct_customer_trade_daily"]["default_time"] == "stat_date"
    assert plan["snap_customer_arrears"]["default_time"] == "snapshot_date"


def test_asset_plan_contains_required_derived_metrics():
    plan = build_asset_plan()
    metrics = {
        metric["biz_name"]: metric
        for model in plan.values()
        for metric in model["metrics"]
    }

    assert metrics["aov_sale"]["expr"] == "ROUND(SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0), 2)"
    assert metrics["unshipped_order_cnt"]["expr"] == "COUNT(DISTINCT order_no)"
    assert metrics["overtime_order_cnt"]["expr"] == (
        "COUNT(DISTINCT CASE WHEN is_overtime = 1 THEN order_no END)"
    )
    assert metrics["dormant_product_cnt"]["expr"] == (
        "COUNT(DISTINCT CASE WHEN days_unsold >= 30 THEN product_id END)"
    )
    assert metrics["overdue_customer_cnt"]["expr"] == (
        "COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END)"
    )
    assert build_asset_plan()["fct_customer_trade_daily"]["obsolete_metrics"] == [
        "new_deal_customer_cnt"
    ]
    assert "消费金额" in build_asset_plan()["fct_customer_trade_daily"]["metric_aliases"]["customer_gmv"]


def test_asset_plan_registers_store_synonyms_for_stall_dimension():
    plan = build_asset_plan()

    for model in plan.values():
        assert model["dimension_aliases"]["stall_id"] == ["店铺", "档口", "门店"]


def test_asset_plan_registers_snapshot_inventory_phrase_alias():
    aliases = build_asset_plan()["snap_product_inventory"]["metric_aliases"]["stock_qty"]

    assert "当前期末库存" in aliases
