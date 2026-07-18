"""幂等修正 ChatBI 首期五张单表模型的 Semantic 资产。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from typing import Any

from sqlmodel import Session, select

from apps.semantic.models.orm import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
)
from common.core.db import engine


def build_asset_plan() -> dict[str, dict[str, Any]]:
    """返回与数据库 ID 无关的首期资产目标定义。"""

    return {
        "fct_stall_order_daily": {
            "grain": ["stat_date", "stall_id"],
            "default_time": "stat_date",
            "dimension_aliases": {"stall_id": ["店铺", "档口", "门店"]},
            "metrics": [
                _metric(
                    "aov_sale",
                    "销售订单平均客单价",
                    "ROUND(SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0), 2)",
                    ["销售客单价"],
                ),
                _metric(
                    "aov_booking",
                    "订货订单平均客单价",
                    "ROUND(SUM(gmv_booking) / NULLIF(SUM(order_cnt_booking), 0), 2)",
                    ["订货客单价"],
                ),
                _metric(
                    "aov_total",
                    "订单平均客单价",
                    "ROUND(SUM(gmv_total) / NULLIF(SUM(order_cnt_total), 0), 2)",
                    ["总客单价"],
                ),
            ],
        },
        "snap_unshipped_order": {
            "grain": ["snapshot_date", "order_no"],
            "default_time": "snapshot_date",
            "dimension_aliases": {"stall_id": ["店铺", "档口", "门店"]},
            "obsolete_metrics": ["overtime_unshipped_order_cnt"],
            "metrics": [
                _metric("unshipped_order_cnt", "未发订单数", "COUNT(DISTINCT order_no)", ["未发订单笔数"]),
                _metric(
                    "overtime_order_cnt",
                    "超时未发订单数",
                    "COUNT(DISTINCT CASE WHEN is_overtime = 1 THEN order_no END)",
                    ["超时订单数", "超时未发订单笔数"],
                ),
            ],
        },
        "snap_product_inventory": {
            "grain": ["snapshot_date", "stall_id", "product_id"],
            "default_time": "snapshot_date",
            "dimension_aliases": {"stall_id": ["店铺", "档口", "门店"]},
            "obsolete_metrics": ["dormant_product_cnt_30d"],
            "metric_aliases": {"stock_qty": ["库存总量", "库存量"]},
            "metrics": [
                _metric(
                    "negative_stock_product_cnt",
                    "负库存商品数",
                    "COUNT(DISTINCT CASE WHEN stock_qty < 0 THEN product_id END)",
                    ["负库存商品数量"],
                ),
                _metric(
                    "dormant_product_cnt",
                    "连续30天未动销商品数",
                    "COUNT(DISTINCT CASE WHEN days_unsold >= 30 THEN product_id END)",
                    ["30天未动销商品数", "滞销商品数"],
                ),
            ],
        },
        "fct_customer_trade_daily": {
            "grain": ["stat_date", "customer_id", "stall_id"],
            "default_time": "stat_date",
            "dimension_aliases": {"stall_id": ["店铺", "档口", "门店"]},
            "obsolete_metrics": ["new_deal_customer_cnt"],
            "metric_aliases": {
                "customer_gmv": ["消费金额", "客户GMV", "GMV"],
                "order_cnt": ["订单数", "客户订单数"],
            },
            "metrics": [
                _metric(
                    "new_customer_cnt",
                    "新增成交客户数",
                    "COUNT(DISTINCT CASE WHEN is_first_deal = 1 THEN customer_id END)",
                    ["首单成交客户数"],
                ),
            ],
        },
        "snap_customer_arrears": {
            "grain": ["snapshot_date", "customer_id", "stall_id"],
            "default_time": "snapshot_date",
            "dimension_aliases": {"stall_id": ["店铺", "档口", "门店"]},
            "metric_aliases": {
                "arrears_amt": ["欠款金额", "客户欠款总金额"],
                "overdue_amt": ["逾期金额"],
            },
            "metrics": [
                _metric(
                    "overdue_customer_cnt",
                    "逾期客户数",
                    "COUNT(DISTINCT CASE WHEN overdue_amt > 0 THEN customer_id END)",
                    ["逾期客户数量"],
                ),
                _metric(
                    "arrears_customer_cnt",
                    "欠款客户数",
                    "COUNT(DISTINCT CASE WHEN arrears_amt > 0 THEN customer_id END)",
                    ["欠款客户数量"],
                ),
            ],
        },
    }


def _metric(biz_name: str, name: str, expr: str, aliases: list[str]) -> dict[str, Any]:
    return {"biz_name": biz_name, "name": name, "expr": expr, "aliases": aliases}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="修正 ChatBI 首期 Semantic 资产")
    parser.add_argument("--dataset-id", type=int, default=243)
    parser.add_argument("--oid", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def configure_assets(session: Session, dataset_id: int, oid: int, dry_run: bool) -> dict[str, int]:
    """按稳定业务标识更新资产；重复执行不会创建重复指标。"""

    dataset = session.get(SemanticDataset, dataset_id)
    if dataset is None or dataset.oid != oid or dataset.status != 1:
        raise ValueError(f"Semantic 数据集不存在: {dataset_id}")
    model_ids = [
        int(item["id"])
        for item in dataset.data_set_detail.get("dataSetModelConfigs", [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    models = session.exec(
        select(SemanticModel).where(
            SemanticModel.oid == oid,
            SemanticModel.id.in_(model_ids),
            SemanticModel.status == 1,
        )
    ).all()
    model_by_table = {str(model.table_name or ""): model for model in models}
    plan = build_asset_plan()
    missing_tables = sorted(set(plan) - set(model_by_table))
    if missing_tables:
        raise ValueError(f"数据集缺少首期模型: {', '.join(missing_tables)}")

    summary = {"created": 0, "updated": 0, "unchanged": 0}
    for table_name, target in plan.items():
        model = model_by_table[table_name]
        _update_value(model, "model_grain", target["grain"], summary)
        _update_value(model, "default_time_field", target["default_time"], summary)

        dimensions = session.exec(
            select(SemanticDimension).where(
                SemanticDimension.oid == oid,
                SemanticDimension.model_id == model.id,
                SemanticDimension.status == 1,
            )
        ).all()
        for dimension in dimensions:
            should_default = dimension.biz_name == target["default_time"]
            _update_value(dimension, "is_default_time", should_default, summary)
            aliases = (target.get("dimension_aliases") or {}).get(dimension.biz_name, [])
            if aliases:
                merged_aliases = list(dict.fromkeys([*(dimension.alias or []), *aliases]))
                _update_value(dimension, "alias", merged_aliases, summary)

        existing_metrics = {
            metric.biz_name: metric
            for metric in session.exec(
                select(SemanticMetric).where(
                    SemanticMetric.oid == oid,
                    SemanticMetric.model_id == model.id,
                    SemanticMetric.status == 1,
                )
            ).all()
        }
        obsolete_metrics = set(target.get("obsolete_metrics") or [])
        for obsolete_biz_name in obsolete_metrics:
            obsolete = existing_metrics.get(obsolete_biz_name)
            if obsolete is not None:
                _update_value(obsolete, "status", 0, summary)
        for biz_name, aliases in (target.get("metric_aliases") or {}).items():
            metric = existing_metrics.get(biz_name)
            if metric is None:
                continue
            merged_aliases = list(dict.fromkeys([*(metric.alias or []), *aliases]))
            _update_value(metric, "alias", merged_aliases, summary)
        for metric_target in target["metrics"]:
            metric = existing_metrics.get(metric_target["biz_name"])
            if metric is None:
                metric = SemanticMetric(
                    oid=oid,
                    model_id=int(model.id),
                    name=metric_target["name"],
                    biz_name=metric_target["biz_name"],
                )
                session.add(metric)
                summary["created"] += 1
            _configure_metric(metric, metric_target, summary, count_unchanged=metric_target["biz_name"] in existing_metrics)

    if not dry_run:
        dataset.schema_version += 1
        dataset.index_version += 1
        session.add(dataset)
        session.commit()
    else:
        session.rollback()
    return summary


def _configure_metric(
    metric: SemanticMetric,
    target: dict[str, Any],
    summary: dict[str, int],
    *,
    count_unchanged: bool,
) -> None:
    params = {
        "metricDefineType": "MEASURE",
        "metricDefineByMeasureParams": {
            "expr": target["expr"],
            "measures": [],
        },
    }
    changed = False
    for field_name, value in (
        ("name", target["name"]),
        ("alias", deepcopy(target["aliases"])),
        ("define_type", "MEASURE"),
        ("default_agg", "NONE"),
        ("expr", target["expr"]),
        ("fields", _expression_fields(target["expr"])),
        ("type_params", params),
        ("is_publish", True),
    ):
        if getattr(metric, field_name) == value:
            continue
        setattr(metric, field_name, value)
        changed = True
    if count_unchanged:
        summary["updated" if changed else "unchanged"] += 1


def _expression_fields(expr: str) -> list[str]:
    candidates = (
        "gmv_sale",
        "order_cnt_sale",
        "gmv_booking",
        "order_cnt_booking",
        "gmv_total",
        "order_cnt_total",
        "order_no",
        "is_overtime",
        "stock_qty",
        "product_id",
        "days_unsold",
        "is_first_deal",
        "overdue_amt",
        "arrears_amt",
        "customer_id",
    )
    return [field for field in candidates if field in expr]


def _update_value(instance: Any, field_name: str, value: Any, summary: dict[str, int]) -> None:
    if getattr(instance, field_name) == value:
        summary["unchanged"] += 1
        return
    setattr(instance, field_name, value)
    summary["updated"] += 1


def main() -> None:
    args = parse_args()
    with Session(engine) as session:
        summary = configure_assets(session, args.dataset_id, args.oid, args.dry_run)
    mode = "预览" if args.dry_run else "执行"
    print(f"{mode}完成：新增 {summary['created']}，更新 {summary['updated']}，未变化 {summary['unchanged']}")


if __name__ == "__main__":
    main()
