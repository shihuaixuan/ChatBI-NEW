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

    assert metrics["aov_sale"]["expr"] == "SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0)"
    assert metrics["unshipped_order_cnt"]["expr"] == "COUNT(DISTINCT order_no)"
    assert metrics["overdue_customer_cnt"]["expr"] == (
        "COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END)"
    )
