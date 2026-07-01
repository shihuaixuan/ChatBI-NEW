"""向外部业务数据源写入 ChatBI 首期验证模拟数据。"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pymysql
from sqlmodel import Session

from apps.datasource.models.datasource import CoreDatasource
from apps.datasource.utils.utils import aes_decrypt
from common.core.db import engine

TABLES = (
    "fct_stall_order_daily",
    "snap_unshipped_order",
    "snap_product_inventory",
    "fct_customer_trade_daily",
    "snap_customer_arrears",
)

CUSTOMER_NAMES = (
    "华北大客",
    "新客小周",
    "杭州衣阁",
    "苏州云裳",
    "上海风尚",
    "南京布语",
    "合肥优品",
    "宁波潮集",
    "嘉兴衣仓",
    "绍兴纺客",
    "温州名品",
    "义乌精选",
)

PRODUCT_NAMES = (
    "春款针织打底",
    "羊毛混纺外套",
    "轻薄防晒衫",
    "高腰阔腿裤",
    "复古牛仔夹克",
    "纯棉基础短袖",
    "通勤西装套装",
    "法式碎花连衣裙",
    "休闲连帽卫衣",
    "简约针织开衫",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 ChatBI 首期验证模拟数据")
    parser.add_argument("--datasource-id", type=int, default=10, help="目标外部数据源 ID")
    parser.add_argument("--days", type=int, default=30, help="生成的连续自然日数量")
    parser.add_argument("--end-date", default="2026-06-30", help="数据结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--seed", type=int, default=20260630, help="固定随机种子")
    return parser.parse_args()


def load_connection_config(datasource_id: int) -> dict:
    """从 SQLBot 元数据库读取外部数据源配置，但不输出敏感信息。"""

    with Session(engine) as session:
        datasource = session.get(CoreDatasource, datasource_id)
        if datasource is None:
            raise ValueError(f"数据源不存在: {datasource_id}")
        if datasource.type.lower() != "mysql":
            raise ValueError(f"当前脚本仅支持 MySQL，实际类型为: {datasource.type}")
        config = json.loads(aes_decrypt(datasource.configuration))
    return config


def customer_catalog() -> dict[tuple[int, int], list[tuple[str, str]]]:
    """为每个档口创建稳定且互不冲突的客户主数据。"""

    catalog: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for seller_id in (10001, 10002):
        for stall_offset in range(1, 4):
            stall_id = seller_id * 10 + stall_offset
            customers = []
            for customer_offset, name in enumerate(CUSTOMER_NAMES, start=1):
                customer_id = f"C{seller_id}{stall_offset:02d}{customer_offset:03d}"
                customers.append((customer_id, name))
            catalog[(seller_id, stall_id)] = customers
    return catalog


def build_trade_and_order_rows(
    rng: random.Random,
    start_date: date,
    days: int,
) -> tuple[list[tuple], list[tuple]]:
    """先生成客户交易，再按档口和日期汇总订单事实，保证两表口径基本一致。"""

    catalog = customer_catalog()
    trade_rows: list[tuple] = []
    order_agg: dict[tuple[date, int, int], dict] = defaultdict(
        lambda: {
            "sale_order": 0,
            "booking_order": 0,
            "sale_qty": 0,
            "booking_qty": 0,
            "sale_gmv": Decimal("0"),
            "booking_gmv": Decimal("0"),
            "sale_customers": set(),
            "booking_customers": set(),
        }
    )
    first_deal_date: dict[str, date] = {}

    for day_offset in range(days):
        stat_date = start_date + timedelta(days=day_offset)
        weekday_factor = Decimal("1.25") if stat_date.weekday() in {4, 5} else Decimal("1.00")
        trend_factor = Decimal("1") + Decimal(day_offset) / Decimal("100")
        for (seller_id, stall_id), customers in catalog.items():
            selected_customers = rng.sample(customers, 3)
            for customer_id, customer_name in selected_customers:
                channel_type = "线上" if rng.random() < 0.72 else "线下"
                order_type = "sale" if rng.random() < 0.78 else "booking"
                order_cnt = rng.randint(1, 4)
                item_qty = order_cnt * rng.randint(2, 12)
                unit_price = Decimal(rng.randint(80, 480))
                customer_gmv = (Decimal(item_qty) * unit_price * weekday_factor * trend_factor).quantize(
                    Decimal("0.01")
                )
                first_deal_date.setdefault(customer_id, stat_date)
                is_first_deal = int(first_deal_date[customer_id] == stat_date)
                last_order_time = datetime.combine(
                    stat_date,
                    time(hour=rng.randint(9, 21), minute=rng.randint(0, 59)),
                )
                trade_rows.append(
                    (
                        stat_date,
                        seller_id,
                        stall_id,
                        customer_id,
                        customer_name,
                        channel_type,
                        customer_gmv,
                        order_cnt,
                        item_qty,
                        is_first_deal,
                        1,
                        last_order_time,
                    )
                )

                summary = order_agg[(stat_date, seller_id, stall_id)]
                summary[f"{order_type}_order"] += order_cnt
                summary[f"{order_type}_qty"] += item_qty
                summary[f"{order_type}_gmv"] += customer_gmv
                summary[f"{order_type}_customers"].add(customer_id)

    order_rows: list[tuple] = []
    for (stat_date, seller_id, stall_id), summary in sorted(order_agg.items()):
        sale_order = summary["sale_order"]
        booking_order = summary["booking_order"]
        sale_qty = summary["sale_qty"]
        booking_qty = summary["booking_qty"]
        sale_gmv = summary["sale_gmv"]
        booking_gmv = summary["booking_gmv"]
        all_customers = summary["sale_customers"] | summary["booking_customers"]
        order_rows.append(
            (
                stat_date,
                seller_id,
                stall_id,
                sale_order,
                booking_order,
                sale_order + booking_order,
                sale_qty,
                booking_qty,
                sale_qty + booking_qty,
                sale_gmv,
                booking_gmv,
                sale_gmv + booking_gmv,
                len(summary["sale_customers"]),
                len(summary["booking_customers"]),
                len(all_customers),
            )
        )
    return trade_rows, order_rows


def build_inventory_rows(rng: random.Random, snapshot_date: date) -> list[tuple]:
    """生成含负库存、低库存和滞销状态的商品库存快照。"""

    rows: list[tuple] = []
    for seller_id in (10001, 10002):
        for stall_offset in range(1, 4):
            stall_id = seller_id * 10 + stall_offset
            for product_offset in range(1, 26):
                product_id = f"P{seller_id}{stall_offset:02d}{product_offset:03d}"
                goods_no = f"G{stall_offset:02d}{product_offset:04d}"
                product_name = PRODUCT_NAMES[(product_offset - 1) % len(PRODUCT_NAMES)]
                days_unsold = rng.choice([0, 1, 2, 5, 8, 15, 29, 31, 45, 60])
                sales_qty_30d = 0 if days_unsold >= 30 else rng.randint(3, 260)
                sales_qty_7d = 0 if days_unsold >= 7 else min(sales_qty_30d, rng.randint(1, 80))
                stock_qty = rng.randint(-25, 600)
                total_sales_qty = rng.randint(sales_qty_30d, 20000)
                last_sale_time = None
                if days_unsold < 365:
                    last_sale_time = datetime.combine(
                        snapshot_date - timedelta(days=days_unsold),
                        time(hour=rng.randint(9, 21), minute=rng.randint(0, 59)),
                    )
                rows.append(
                    (
                        snapshot_date,
                        seller_id,
                        stall_id,
                        product_id,
                        goods_no,
                        product_name,
                        stock_qty,
                        total_sales_qty,
                        sales_qty_7d,
                        sales_qty_30d,
                        last_sale_time,
                        days_unsold,
                        int(stock_qty < 0),
                        int(days_unsold >= 30),
                    )
                )
    return rows


def build_unshipped_rows(rng: random.Random, snapshot_date: date) -> list[tuple]:
    """生成包含正常待发和超时待发的订单快照。"""

    rows: list[tuple] = []
    catalog = customer_catalog()
    stall_keys = list(catalog)
    for order_offset in range(1, 41):
        seller_id, stall_id = stall_keys[(order_offset - 1) % len(stall_keys)]
        customer_id, customer_name = rng.choice(catalog[(seller_id, stall_id)])
        item_qty = rng.randint(1, 30)
        shipped_qty = rng.randint(0, max(0, item_qty - 1))
        unshipped_qty = item_qty - shipped_qty
        waiting_days = rng.choice([1, 2, 3, 5, 7, 12, 16, 20, 28])
        pend_ship_time = datetime.combine(
            snapshot_date - timedelta(days=waiting_days),
            time(hour=rng.randint(8, 18), minute=rng.randint(0, 59)),
        )
        deadline_time = pend_ship_time + timedelta(days=15)
        overtime_days = max(0, (snapshot_date - deadline_time.date()).days)
        product_name = rng.choice(PRODUCT_NAMES)
        rows.append(
            (
                snapshot_date,
                seller_id,
                stall_id,
                f"USO{snapshot_date:%Y%m%d}{order_offset:04d}",
                customer_id,
                customer_name,
                Decimal(item_qty * rng.randint(100, 600)).quantize(Decimal("0.01")),
                item_qty,
                shipped_qty,
                unshipped_qty,
                pend_ship_time,
                deadline_time,
                overtime_days,
                int(overtime_days > 0),
                f"{product_name}等{rng.randint(1, 4)}款商品",
            )
        )
    return rows


def build_arrears_rows(rng: random.Random, snapshot_date: date) -> list[tuple]:
    """生成不同账龄和逾期程度的客户欠款快照。"""

    rows: list[tuple] = []
    all_customers = [
        (seller_id, stall_id, customer_id, customer_name)
        for (seller_id, stall_id), customers in customer_catalog().items()
        for customer_id, customer_name in customers
    ]
    for seller_id, stall_id, customer_id, customer_name in rng.sample(all_customers, 50):
        arrears_days = rng.choice([2, 5, 10, 20, 31, 45, 60, 90])
        arrears_amt = Decimal(rng.randint(500, 120000)).quantize(Decimal("0.01"))
        is_overdue = int(arrears_days > 30)
        overdue_ratio = Decimal(str(rng.uniform(0.35, 1.0))) if is_overdue else Decimal("0")
        overdue_amt = (arrears_amt * overdue_ratio).quantize(Decimal("0.01"))
        earliest_date = snapshot_date - timedelta(days=arrears_days)
        latest_date = min(snapshot_date, earliest_date + timedelta(days=rng.randint(0, arrears_days)))
        rows.append(
            (
                snapshot_date,
                seller_id,
                stall_id,
                customer_id,
                customer_name,
                arrears_amt,
                rng.randint(1, 6),
                earliest_date,
                latest_date,
                arrears_days,
                is_overdue,
                overdue_amt,
            )
        )
    return rows


def insert_rows(connection, rows_by_table: dict[str, list[tuple]]) -> None:
    """在一个事务中清理旧模拟数据并批量写入新数据。"""

    statements = {
        "fct_stall_order_daily": """
            INSERT INTO fct_stall_order_daily (
                stat_date, seller_id, stall_id,
                order_cnt_sale, order_cnt_booking, order_cnt_total,
                item_qty_sale, item_qty_booking, item_qty_total,
                gmv_sale, gmv_booking, gmv_total,
                order_customer_cnt_sale, order_customer_cnt_booking, order_customer_cnt_total
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        "snap_unshipped_order": """
            INSERT INTO snap_unshipped_order (
                snapshot_date, seller_id, stall_id, order_no,
                customer_id, customer_name, order_amount,
                item_qty, shipped_qty, unshipped_qty,
                pend_ship_time, deadline_time, overtime_days, is_overtime, product_summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        "snap_product_inventory": """
            INSERT INTO snap_product_inventory (
                snapshot_date, seller_id, stall_id, product_id,
                goods_no, product_name, stock_qty, total_sales_qty,
                sales_qty_7d, sales_qty_30d, last_sale_time, days_unsold,
                is_negative_stock, is_dormant_30d
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        "fct_customer_trade_daily": """
            INSERT INTO fct_customer_trade_daily (
                stat_date, seller_id, stall_id, customer_id,
                customer_name, channel_type, customer_gmv,
                order_cnt, item_qty, is_first_deal, is_active, last_order_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        "snap_customer_arrears": """
            INSERT INTO snap_customer_arrears (
                snapshot_date, seller_id, stall_id, customer_id,
                customer_name, arrears_amt, arrears_bill_cnt,
                earliest_arrears_date, latest_arrears_date,
                arrears_days, is_overdue, overdue_amt
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
    }

    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"DELETE FROM `{table}`")
        for table, rows in rows_by_table.items():
            cursor.executemany(statements[table], rows)


def main() -> None:
    args = parse_args()
    if args.days <= 0:
        raise ValueError("--days 必须大于 0")

    end_date = date.fromisoformat(args.end_date)
    start_date = end_date - timedelta(days=args.days - 1)
    rng = random.Random(args.seed)
    trade_rows, order_rows = build_trade_and_order_rows(rng, start_date, args.days)
    rows_by_table = {
        "fct_stall_order_daily": order_rows,
        "snap_unshipped_order": build_unshipped_rows(rng, end_date),
        "snap_product_inventory": build_inventory_rows(rng, end_date),
        "fct_customer_trade_daily": trade_rows,
        "snap_customer_arrears": build_arrears_rows(rng, end_date),
    }

    config = load_connection_config(args.datasource_id)
    connection = pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["username"],
        password=config["password"],
        database=config["database"],
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        insert_rows(connection, rows_by_table)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(
        json.dumps(
            {
                "database": config["database"],
                "date_range": [start_date.isoformat(), end_date.isoformat()],
                "seed": args.seed,
                "row_counts": {table: len(rows) for table, rows in rows_by_table.items()},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
