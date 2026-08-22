"""只读验证 dataset 243 层级候选的真实基数关系（datasource 13）。

判定标准（构成 FIXED_LEVEL 层级要求下级->上级多对一）：
- 每个下级值只映射到一个上级值；
- 且存在上级值拥有多个下级（否则退化为一对一，无下钻意义）。
"""

from __future__ import annotations

from sqlmodel import Session

from apps.datasource.composition import build_datasource_connection_service
from common.core.db import engine

CHECKS = [
    (
        "商家(seller_id) -> 档口(stall_id)",
        "fct_stall_order_daily",
        "seller_id",
        "stall_id",
    ),
    (
        "货号(goods_no) -> 商品(product_id)",
        "snap_product_inventory",
        "goods_no",
        "product_id",
    ),
]


def main() -> None:
    with Session(engine) as session:
        connection_service = build_datasource_connection_service(session)

        def run(sql: str) -> list[dict]:
            payload = connection_service.execute_query(13, sql)
            return payload.get("data", payload) if isinstance(payload, dict) else payload

        for label, table, parent, child in CHECKS:
            print(f"=== {label}  ({table}) ===")
            pairs = run(
                f"SELECT COUNT(*) AS pair_cnt FROM (SELECT DISTINCT {parent}, {child} FROM {table}) t"
            )
            print(f"distinct (parent, child) pairs: {pairs}")
            child_multi = run(
                f"SELECT COUNT(*) AS cnt FROM (SELECT {child} FROM {table} "
                f"GROUP BY {child} HAVING COUNT(DISTINCT {parent}) > 1) t"
            )
            print(f"children mapping to >1 parent (violates hierarchy): {child_multi}")
            parent_multi = run(
                f"SELECT COUNT(*) AS cnt FROM (SELECT {parent} FROM {table} "
                f"GROUP BY {parent} HAVING COUNT(DISTINCT {child}) > 1) t"
            )
            print(f"parents having >1 children (drilldown is meaningful): {parent_multi}")
            sample = run(
                f"SELECT {parent}, COUNT(DISTINCT {child}) AS children FROM {table} "
                f"GROUP BY {parent} ORDER BY children DESC LIMIT 5"
            )
            print(f"top parents by children count: {sample}")
            print()

        print("=== 数据时间范围与规模 ===")
        print(run("SELECT MIN(stat_date) AS min_d, MAX(stat_date) AS max_d, "
                  "COUNT(DISTINCT stall_id) AS stalls FROM fct_stall_order_daily"))
        print(run("SELECT COUNT(DISTINCT channel_type) AS channels FROM fct_customer_trade_daily"))
        print(run("SELECT channel_type, COUNT(*) AS row_cnt FROM fct_customer_trade_daily "
                  "GROUP BY channel_type ORDER BY row_cnt DESC LIMIT 5"))


if __name__ == "__main__":
    main()
