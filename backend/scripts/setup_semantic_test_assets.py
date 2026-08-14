"""通过前端同源 HTTP API 配置 9 张测试表及业务指标。"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pymysql

from apps.datasource.utils.utils import aes_encrypt

API_BASE = os.getenv("SQLBOT_API_BASE", "http://127.0.0.1:8000/api/v1")
ADMIN_ACCOUNT = os.getenv("SQLBOT_ADMIN_ACCOUNT", "admin")
ADMIN_PASSWORD = os.environ["SQLBOT_ADMIN_PASSWORD"]
MYSQL_PASSWORD = os.environ["SQLBOT_TEST_MYSQL_PASSWORD"]
DATASOURCE_NAME = "语义层九表测试库"
DOMAIN_BIZ_NAME = "semantic_business_test"

TABLES = [
    "track_event",
    "user_favorite",
    "order_sale",
    "order_sale_item",
    "order_purchase",
    "order_purchase_item",
    "seller_customer",
    "goods_spu",
    "goods_sku",
]

TABLE_MODEL_SPECS = {
    "track_event": {
        "name": "用户行为事件",
        "identifiers": {"event_id"},
        "dimensions": {
            "sellerId",
            "user_unique_id",
            "user_id",
            "platform",
            "event_type",
            "page_name",
            "event_time",
        },
        "time": {"event_time"},
        "measures": {},
        "filter": "del_flag = '0' AND platform = 'MINI'",
    },
    "user_favorite": {
        "name": "用户关注记录",
        "identifiers": {"favorite_id"},
        "dimensions": {"scene_id", "scene_type", "user_id", "create_time"},
        "time": {"create_time"},
        "measures": {},
        "filter": "del_flag = '0' AND scene_type = 1",
    },
    "order_sale": {
        "name": "销售订单",
        "identifiers": {"order_no"},
        "dimensions": {"seller_id", "customer_id", "order_date"},
        "time": {"order_date"},
        "measures": {
            "total_quantity": "SUM",
            "total_amount": "SUM",
            "received_amount": "SUM",
        },
        "filter": "del_flag = '0'",
    },
    "order_sale_item": {
        "name": "销售订单明细",
        "identifiers": {"item_id"},
        "dimensions": {"order_no", "spu_id", "style_no", "goods_name"},
        "time": set(),
        "measures": {"quantity": "SUM", "total_price": "SUM"},
        "filter": "del_flag = '0'",
    },
    "order_purchase": {
        "name": "订货订单",
        "identifiers": {"order_no"},
        "dimensions": {"seller_id", "customer_id", "order_date", "ship_status"},
        "time": {"order_date"},
        "measures": {"total_quantity": "SUM", "total_amount": "SUM"},
        "filter": "del_flag = '0'",
    },
    "order_purchase_item": {
        "name": "订货订单明细",
        "identifiers": {"item_id"},
        "dimensions": {"order_no", "spu_id", "style_no", "goods_name"},
        "time": set(),
        "measures": {
            "total_price": "SUM",
            "order_quantity": "SUM",
            "shipped_quantity": "SUM",
        },
        "filter": "del_flag = '0'",
    },
    "seller_customer": {
        "name": "档口客户",
        "identifiers": {"id"},
        "dimensions": {"seller_id", "name", "phone", "create_time"},
        "time": {"create_time"},
        "measures": {},
        "filter": "del_flag = '0'",
    },
    "goods_spu": {
        "name": "档口商品",
        "identifiers": {"id"},
        "dimensions": {"seller_id", "style_no", "goods_name"},
        "time": set(),
        "measures": {},
        "filter": "del_flag = '0'",
    },
    "goods_sku": {
        "name": "商品库存",
        "identifiers": {"sku_id"},
        "dimensions": {"spu_id", "seller_id", "snapshot_time"},
        "time": {"snapshot_time"},
        "measures": {"stock": "SUM"},
        "filter": "del_flag = '0'",
    },
}


SQL_MODELS: dict[str, dict[str, Any]] = {
    "traffic_activity": {
        "name": "档口行为统一明细",
        "sql": """
SELECT CONCAT('event:', event_id) AS activity_id, sellerId AS seller_id,
       COALESCE(user_unique_id, user_id) AS user_id, event_time,
       event_type, page_name
FROM track_event
WHERE del_flag = '0' AND platform = 'MINI'
UNION ALL
SELECT CONCAT('favorite:', favorite_id), scene_id, user_id, create_time,
       'FOLLOW', NULL
FROM user_favorite
WHERE del_flag = '0' AND scene_type = 1
""".strip(),
        "fields": [
            ("activity_id", "VARCHAR", "行为记录", "IDENTIFIER"),
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("user_id", "VARCHAR", "用户", "DIMENSION"),
            ("event_time", "DATETIME", "行为时间", "TIME"),
            ("event_type", "VARCHAR", "行为类型", "DIMENSION"),
            ("page_name", "VARCHAR", "页面名称", "DIMENSION"),
        ],
    },
    "order_all": {
        "name": "销售订货统一订单",
        "sql": """
SELECT CONCAT('SALE:', order_no) AS order_key, order_no, seller_id, customer_id,
       order_date, total_quantity, total_amount, received_amount,
       NULL AS ship_status, 'SALE' AS order_source
FROM order_sale WHERE del_flag = '0'
UNION ALL
SELECT CONCAT('PURCHASE:', order_no), order_no, seller_id, customer_id,
       order_date, total_quantity, total_amount, 0,
       ship_status, 'PURCHASE'
FROM order_purchase WHERE del_flag = '0'
""".strip(),
        "fields": [
            ("order_key", "VARCHAR", "统一订单键", "IDENTIFIER"),
            ("order_no", "VARCHAR", "订单号", "DIMENSION"),
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("customer_id", "BIGINT", "客户", "DIMENSION"),
            ("order_date", "DATETIME", "下单时间", "TIME"),
            ("total_quantity", "BIGINT", "商品件数", "MEASURE"),
            ("total_amount", "DECIMAL", "订单金额", "MEASURE"),
            ("received_amount", "DECIMAL", "已收金额", "MEASURE"),
            ("ship_status", "TINYINT", "发货状态", "DIMENSION"),
            ("order_source", "VARCHAR", "订单来源", "DIMENSION"),
        ],
    },
    "unshipped_order_detail": {
        "name": "未发货订单明细",
        "sql": """
SELECT opi.item_id, op.seller_id, op.order_no, op.customer_id,
       sc.name AS customer_name, opi.spu_id, opi.style_no, opi.goods_name,
       opi.total_price, opi.order_quantity, opi.shipped_quantity,
       opi.order_quantity - opi.shipped_quantity AS unshipped_quantity,
       op.order_date AS pending_ship_date,
       DATE_ADD(op.order_date, INTERVAL 15 DAY) AS timeout_date,
       GREATEST(DATEDIFF(CURRENT_DATE, op.order_date), 0) AS overdue_days,
       CASE WHEN DATEDIFF(CURRENT_DATE, op.order_date) > 15 THEN '超时' ELSE '未超时' END AS timeout_status
FROM order_purchase op
JOIN order_purchase_item opi ON op.order_no = opi.order_no AND opi.del_flag = '0'
LEFT JOIN seller_customer sc ON op.customer_id = sc.id AND op.seller_id = sc.seller_id AND sc.del_flag = '0'
WHERE op.del_flag = '0' AND op.ship_status IN (0, 1)
""".strip(),
        "fields": [
            ("item_id", "BIGINT", "订单明细", "IDENTIFIER"),
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("order_no", "VARCHAR", "订单号", "DIMENSION"),
            ("customer_id", "BIGINT", "客户", "DIMENSION"),
            ("customer_name", "VARCHAR", "客户名称", "DIMENSION"),
            ("spu_id", "BIGINT", "商品", "DIMENSION"),
            ("style_no", "VARCHAR", "货号", "DIMENSION"),
            ("goods_name", "VARCHAR", "商品名称", "DIMENSION"),
            ("total_price", "DECIMAL", "明细金额", "MEASURE"),
            ("order_quantity", "BIGINT", "订货件数", "MEASURE"),
            ("shipped_quantity", "BIGINT", "已发件数", "MEASURE"),
            ("unshipped_quantity", "BIGINT", "未发件数", "MEASURE"),
            ("pending_ship_date", "DATETIME", "待发货时间", "TIME"),
            ("timeout_date", "DATETIME", "超时时间", "DIMENSION"),
            ("overdue_days", "INT", "超时天数", "DIMENSION"),
            ("timeout_status", "VARCHAR", "是否超时", "DIMENSION"),
        ],
    },
    "product_inventory_analysis": {
        "name": "商品库存分析",
        "sql": """
SELECT spu.seller_id, spu.id AS spu_id, spu.style_no, spu.goods_name,
       SUM(sku.stock) AS total_stock,
       SUM(CASE WHEN sku.stock < 0 THEN sku.stock ELSE 0 END) AS negative_stock_quantity,
       SUM(CASE WHEN sku.stock < 0 THEN 1 ELSE 0 END) AS negative_sku_count,
       COALESCE(sale.quantity_7d, 0) AS quantity_7d,
       COALESCE(sale.quantity_30d, 0) AS quantity_30d,
       sale.last_sale_time,
       CASE WHEN sale.last_sale_time IS NULL THEN NULL ELSE DATEDIFF(CURRENT_DATE, sale.last_sale_time) END AS unsold_days,
       CASE WHEN COALESCE(sale.quantity_30d, 0) = 0 THEN 1 ELSE 0 END AS inactive_spu_flag,
       MAX(sku.snapshot_time) AS snapshot_time
FROM goods_spu spu
JOIN goods_sku sku ON spu.id = sku.spu_id AND spu.seller_id = sku.seller_id AND sku.del_flag = '0'
LEFT JOIN (
    SELECT osi.spu_id, os.seller_id,
           SUM(CASE WHEN os.order_date >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY) THEN osi.quantity ELSE 0 END) AS quantity_7d,
           SUM(CASE WHEN os.order_date >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY) THEN osi.quantity ELSE 0 END) AS quantity_30d,
           MAX(os.order_date) AS last_sale_time
    FROM order_sale_item osi JOIN order_sale os ON osi.order_no = os.order_no
    WHERE osi.del_flag = '0' AND os.del_flag = '0'
    GROUP BY osi.spu_id, os.seller_id
) sale ON spu.id = sale.spu_id AND spu.seller_id = sale.seller_id
WHERE spu.del_flag = '0'
GROUP BY spu.seller_id, spu.id, spu.style_no, spu.goods_name,
         sale.quantity_7d, sale.quantity_30d, sale.last_sale_time
""".strip(),
        "fields": [
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("spu_id", "BIGINT", "商品", "IDENTIFIER"),
            ("style_no", "VARCHAR", "货号", "DIMENSION"),
            ("goods_name", "VARCHAR", "商品名称", "DIMENSION"),
            ("total_stock", "BIGINT", "当前库存件数", "MEASURE"),
            ("negative_stock_quantity", "BIGINT", "负库存数量", "MEASURE"),
            ("negative_sku_count", "BIGINT", "负库存SKU数", "MEASURE"),
            ("quantity_7d", "BIGINT", "近7天销量", "MEASURE"),
            ("quantity_30d", "BIGINT", "近30天销量", "MEASURE"),
            ("last_sale_time", "DATETIME", "最后销售时间", "DIMENSION"),
            ("unsold_days", "INT", "未销售天数", "DIMENSION"),
            ("inactive_spu_flag", "TINYINT", "30天未动销标记", "MEASURE"),
            ("snapshot_time", "DATETIME", "库存观测时间", "TIME"),
        ],
    },
    "visitor_first_visit": {
        "name": "线上用户首次访问",
        "sql": """
SELECT sellerId AS seller_id, COALESCE(user_unique_id, user_id) AS user_id,
       MIN(event_time) AS first_visit_time
FROM track_event
WHERE del_flag = '0' AND platform = 'MINI' AND event_type = 'PAGE_VIEW'
  AND COALESCE(user_unique_id, user_id) IS NOT NULL
GROUP BY sellerId, COALESCE(user_unique_id, user_id)
""".strip(),
        "fields": [
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("user_id", "VARCHAR", "线上用户", "IDENTIFIER"),
            ("first_visit_time", "DATETIME", "首次访问时间", "TIME"),
        ],
    },
    "customer_value": {
        "name": "客户价值分析",
        "sql": """
SELECT sc.seller_id, sc.id AS customer_id, sc.name AS customer_name, sc.phone,
       sc.create_time AS register_time, MIN(os.order_date) AS first_order_time,
       COALESCE(SUM(os.total_amount), 0) AS customer_gmv,
       COUNT(os.order_no) AS customer_order_count,
       COALESCE(SUM(os.total_quantity), 0) AS customer_quantity,
       CASE WHEN COUNT(os.order_no) = 0 THEN '沉默客户' ELSE '已成交客户' END AS customer_status,
       COALESCE(SUM(os.total_amount), 0) /
         NULLIF(SUM(SUM(os.total_amount)) OVER (PARTITION BY sc.seller_id), 0) AS contribution_rate
FROM seller_customer sc
LEFT JOIN order_sale os ON sc.id = os.customer_id AND sc.seller_id = os.seller_id AND os.del_flag = '0'
WHERE sc.del_flag = '0'
GROUP BY sc.seller_id, sc.id, sc.name, sc.phone, sc.create_time
""".strip(),
        "fields": [
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("customer_id", "BIGINT", "客户", "IDENTIFIER"),
            ("customer_name", "VARCHAR", "客户名称", "DIMENSION"),
            ("phone", "VARCHAR", "客户手机号", "DIMENSION"),
            ("register_time", "DATETIME", "注册时间", "TIME"),
            ("first_order_time", "DATETIME", "首次下单时间", "DIMENSION"),
            ("customer_gmv", "DECIMAL", "客户消费金额", "MEASURE"),
            ("customer_order_count", "BIGINT", "客户订单数", "MEASURE"),
            ("customer_quantity", "BIGINT", "客户购买件数", "MEASURE"),
            ("customer_status", "VARCHAR", "客户状态", "DIMENSION"),
            ("contribution_rate", "DECIMAL", "客户贡献率", "MEASURE"),
        ],
    },
    "customer_recent_product": {
        "name": "客户最近购买商品",
        "sql": """
SELECT os.seller_id, os.customer_id, sc.name AS customer_name,
       osi.spu_id, osi.style_no, osi.goods_name, os.order_date,
       osi.quantity, osi.total_price
FROM order_sale os
JOIN order_sale_item osi ON os.order_no = osi.order_no AND osi.del_flag = '0'
LEFT JOIN seller_customer sc ON os.customer_id = sc.id AND os.seller_id = sc.seller_id AND sc.del_flag = '0'
WHERE os.del_flag = '0' AND os.customer_id IS NOT NULL
""".strip(),
        "fields": [
            ("seller_id", "BIGINT", "档口", "DIMENSION"),
            ("customer_id", "BIGINT", "客户", "DIMENSION"),
            ("customer_name", "VARCHAR", "客户名称", "DIMENSION"),
            ("spu_id", "BIGINT", "商品", "DIMENSION"),
            ("style_no", "VARCHAR", "货号", "DIMENSION"),
            ("goods_name", "VARCHAR", "商品名称", "DIMENSION"),
            ("order_date", "DATETIME", "购买时间", "TIME"),
            ("quantity", "BIGINT", "购买件数", "MEASURE"),
            ("total_price", "DECIMAL", "购买金额", "MEASURE"),
        ],
    },
}


class FrontendApi:
    """模拟前端请求封装，只调用公开 HTTP API。"""

    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API_BASE, timeout=100)
        login = self.client.post(
            "/login/access-token",
            data={"username": ADMIN_ACCOUNT, "password": ADMIN_PASSWORD},
        )
        token = self._unwrap(login)["access_token"]
        self.client.headers["X-SQLBOT-TOKEN"] = f"Bearer {token}"

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and "code" in body:
            if body.get("code") != 0:
                raise RuntimeError(f"API_ERROR: {body}")
            return body.get("data")
        return body

    def get(self, path: str, **params: Any) -> Any:
        return self._unwrap(self.client.get(path, params=params or None))

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._unwrap(self.client.post(path, json=payload))

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        return self._unwrap(self.client.put(path, json=payload))

    def delete(self, path: str) -> Any:
        return self._unwrap(self.client.delete(path))


def field_detail(name: str, data_type: str, label: str, role: str) -> dict[str, Any]:
    return {
        "fieldName": name,
        "dataType": data_type,
        "name": label,
        "bizName": name,
        "expr": name,
        "role": role,
    }


def model_detail(
    fields: list[dict[str, Any]], source_type: str, source: str
) -> dict[str, Any]:
    dimensions = []
    identifiers = []
    measures = []
    for item in fields:
        role = item["role"]
        if role == "IDENTIFIER":
            identifiers.append(
                {
                    "name": item["name"],
                    "bizName": item["bizName"],
                    "fieldName": item["fieldName"],
                    "type": "primary",
                }
            )
        if role in {"IDENTIFIER", "DIMENSION", "TIME"}:
            is_time = role == "TIME"
            dimension = {
                "name": item["name"],
                "bizName": item["bizName"],
                "expr": item["expr"],
                "dataType": item["dataType"],
                "type": "partition_time"
                if is_time
                else ("primary_key" if role == "IDENTIFIER" else "categorical"),
                "semanticType": "time" if is_time else None,
                "alias": [],
                "createDimension": True,
            }
            if is_time:
                dimension["dateFormat"] = "yyyy-MM-dd"
                dimension["typeParams"] = {
                    "isPrimary": "true",
                    "timeGranularity": "day",
                }
            dimensions.append(dimension)
        if role == "MEASURE":
            measures.append(
                {
                    "name": item["name"],
                    "bizName": item["bizName"],
                    "expr": item["expr"],
                    "agg": "SUM",
                    "alias": [],
                    "isCreateMetric": 0,
                    "createMetric": False,
                }
            )
    return {
        "queryType": "sql_query" if source_type == "SQL" else "table_query",
        "tableQuery": {"table": source} if source_type == "TABLE" else {},
        "sqlQuery": {"sql": source} if source_type == "SQL" else {},
        "fields": [
            {
                key: item[key]
                for key in ("fieldName", "dataType", "name", "bizName", "expr")
            }
            for item in fields
        ],
        "identifiers": identifiers,
        "dimensions": dimensions,
        "measures": measures,
        "sqlVariables": [],
    }


def ensure_datasource(api: FrontendApi) -> int:
    existing = next(
        (
            item
            for item in api.get("/datasource/list")
            if item["name"] == DATASOURCE_NAME
        ),
        None,
    )
    if existing:
        return int(existing["id"])
    configuration = aes_encrypt(
        (
            '{"host":"localhost","port":3306,"username":"root",'
            f'"password":"{MYSQL_PASSWORD}","database":"sqlbot_semantic_test",'
            '"extraJdbc":"charset=utf8mb4","dbSchema":"","filename":"",'
            '"sheets":[],"mode":"service_name","timeout":30,"lowVersion":false,"ssl":false}'
        )
    ).decode()
    payload = {
        "name": DATASOURCE_NAME,
        "description": "9张原始业务表的语义层验证数据源",
        "type": "mysql",
        "configuration": configuration,
        "tables": [{"table_name": name, "checked": True} for name in TABLES],
        "recommended_config": 1,
    }
    return int(api.post("/datasource/add", payload)["id"])


def ensure_domain(api: FrontendApi) -> int:
    existing = next(
        (
            item
            for item in api.get("/semantic/domains")
            if item["biz_name"] == DOMAIN_BIZ_NAME
        ),
        None,
    )
    if existing:
        return int(existing["id"])
    result = api.post(
        "/semantic/domains",
        {
            "name": "九表业务分析",
            "biz_name": DOMAIN_BIZ_NAME,
            "description": "流量、订单、库存、客户和账款测试主题域",
            "admin": "",
        },
    )
    return int(result["id"])


def ensure_models(
    api: FrontendApi, datasource_id: int, domain_id: int
) -> dict[str, dict[str, Any]]:
    models = {
        item["biz_name"]: item
        for item in api.get("/semantic/models", domain_id=domain_id)
    }
    for table, spec in TABLE_MODEL_SPECS.items():
        if table in models:
            continue
        columns = api.get(
            f"/semantic/datasources/{datasource_id}/tables/{table}/columns"
        )
        fields = []
        for column in columns:
            name = column["field_name"]
            role = "FIELD"
            if name in spec["identifiers"]:
                role = "IDENTIFIER"
            elif name in spec["time"]:
                role = "TIME"
            elif name in spec["dimensions"]:
                role = "DIMENSION"
            elif name in spec["measures"]:
                role = "MEASURE"
            fields.append(
                field_detail(
                    name,
                    column.get("field_type") or "VARCHAR",
                    column.get("field_comment") or name,
                    role,
                )
            )
        payload = {
            "domain_id": domain_id,
            "datasource_id": datasource_id,
            "name": spec["name"],
            "biz_name": table,
            "description": f"物理表 {table} 的基础语义模型",
            "alias": [],
            "source_type": "TABLE",
            "table_name": table,
            "sql": None,
            "filter_sql": spec["filter"],
            "depends": [],
            "model_detail": model_detail(fields, "TABLE", table),
        }
        result = api.post("/semantic/models/create-with-assets", payload)
        models[table] = result["model"]

    for biz_name, spec in SQL_MODELS.items():
        if biz_name in models:
            continue
        fields = [field_detail(*item) for item in spec["fields"]]
        payload = {
            "domain_id": domain_id,
            "datasource_id": datasource_id,
            "name": spec["name"],
            "biz_name": biz_name,
            "description": f"由前端SQL模型定义的{spec['name']}",
            "alias": [],
            "source_type": "SQL",
            "table_name": None,
            "sql": spec["sql"],
            "filter_sql": None,
            "depends": [],
            "model_detail": model_detail(fields, "SQL", spec["sql"]),
        }
        result = api.post("/semantic/models/create-with-assets", payload)
        models[biz_name] = result["model"]
    return models


def field_metric(
    name: str,
    biz_name: str,
    expr: str,
    fields: list[str],
    description: str,
    filter_sql: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "biz_name": biz_name,
        "description": description,
        "define_type": "FIELD",
        "default_agg": "CUSTOM",
        "type_params": {
            "metricDefineType": "FIELD",
            "metricDefineByFieldParams": {
                "expr": expr,
                "filterSql": filter_sql,
                "fields": [
                    {"fieldName": item, "bizName": item, "name": item, "dataType": ""}
                    for item in fields
                ],
            },
        },
    }


def derived_metric(
    name: str,
    biz_name: str,
    expression: str,
    refs: list[str],
    description: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "biz_name": biz_name,
        "description": description,
        "define_type": "METRIC",
        "default_agg": "CUSTOM",
        "expression": expression,
        "refs": refs,
    }


def metric_specs() -> dict[str, list[dict[str, Any]]]:
    track_event_metrics = [
        field_metric(
            "访问次数（PV）",
            "visit_pv",
            "COUNT(event_id)",
            ["event_id"],
            "PAGE_VIEW事件次数",
            "event_type = 'PAGE_VIEW'",
        ),
        field_metric(
            "访问人数（UV）",
            "visit_uv",
            "COUNT(DISTINCT COALESCE(user_unique_id, user_id))",
            ["user_unique_id", "user_id"],
            "PAGE_VIEW去重用户数",
            "event_type = 'PAGE_VIEW'",
        ),
        field_metric(
            "商品点击次数",
            "product_click_pv",
            "COUNT(event_id)",
            ["event_id"],
            "商品详情页访问次数",
            "event_type = 'PAGE_VIEW' AND page_name = 'product_detail'",
        ),
        field_metric(
            "商品点击人数",
            "product_click_uv",
            "COUNT(DISTINCT COALESCE(user_unique_id, user_id))",
            ["user_unique_id", "user_id"],
            "商品详情页去重访问人数",
            "event_type = 'PAGE_VIEW' AND page_name = 'product_detail'",
        ),
        field_metric(
            "分享次数",
            "share_pv",
            "COUNT(event_id)",
            ["event_id"],
            "分享事件次数",
            "event_type = 'SHARE'",
        ),
        field_metric(
            "分享人数",
            "share_uv",
            "COUNT(DISTINCT COALESCE(user_unique_id, user_id))",
            ["user_unique_id", "user_id"],
            "分享事件去重用户数",
            "event_type = 'SHARE'",
        ),
        field_metric(
            "咨询次数",
            "consult_pv",
            "COUNT(event_id)",
            ["event_id"],
            "联系档口事件次数",
            "event_type = 'CONTACT_SUPPLIER'",
        ),
        field_metric(
            "咨询人数",
            "consult_uv",
            "COUNT(DISTINCT COALESCE(user_unique_id, user_id))",
            ["user_unique_id", "user_id"],
            "联系档口去重用户数",
            "event_type = 'CONTACT_SUPPLIER'",
        ),
    ]
    favorite_metrics = [
        field_metric(
            "关注人数",
            "follow_uv",
            "COUNT(DISTINCT user_id)",
            ["user_id"],
            "关注档口的去重用户数",
        ),
    ]
    unified_traffic_metrics = [
        field_metric(
            "访问次数（PV）",
            "visit_pv",
            "COUNT(activity_id)",
            ["activity_id"],
            "PAGE_VIEW事件次数",
            "event_type = 'PAGE_VIEW'",
        ),
        field_metric(
            "访问人数（UV）",
            "visit_uv",
            "COUNT(DISTINCT user_id)",
            ["user_id"],
            "PAGE_VIEW去重用户数",
            "event_type = 'PAGE_VIEW'",
        ),
        field_metric(
            "关注人数",
            "follow_uv",
            "COUNT(DISTINCT user_id)",
            ["user_id"],
            "关注档口的去重用户数",
            "event_type = 'FOLLOW'",
        ),
        field_metric(
            "咨询次数",
            "consult_pv",
            "COUNT(activity_id)",
            ["activity_id"],
            "联系档口事件次数",
            "event_type = 'CONTACT_SUPPLIER'",
        ),
        field_metric(
            "转化人数",
            "conversion_uv",
            "COUNT(DISTINCT user_id)",
            ["user_id"],
            "关注、联系、分享用户合并后的去重人数",
            "event_type IN ('FOLLOW','CONTACT_SUPPLIER','SHARE')",
        ),
        derived_metric(
            "关注率",
            "follow_rate",
            "follow_uv / NULLIF(visit_uv, 0)",
            ["follow_uv", "visit_uv"],
            "关注人数除以访问人数",
        ),
        derived_metric(
            "咨询率",
            "consult_rate",
            "consult_pv / NULLIF(visit_pv, 0)",
            ["consult_pv", "visit_pv"],
            "咨询次数除以访问次数",
        ),
        derived_metric(
            "转化率",
            "conversion_rate",
            "conversion_uv / NULLIF(visit_uv, 0)",
            ["conversion_uv", "visit_uv"],
            "转化人数除以访问人数",
        ),
    ]

    orders = [
        field_metric(
            "销售订单数",
            "sale_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key"],
            "销售订单去重数",
            "order_source = 'SALE'",
        ),
        field_metric(
            "订货订单数",
            "purchase_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key"],
            "订货订单去重数",
            "order_source = 'PURCHASE'",
        ),
        field_metric(
            "总订单数",
            "total_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key"],
            "销售和订货订单去重总数",
        ),
        field_metric(
            "销售商品件数",
            "sale_quantity",
            "SUM(total_quantity)",
            ["total_quantity"],
            "销售订单商品件数",
            "order_source = 'SALE'",
        ),
        field_metric(
            "订货商品件数",
            "purchase_quantity",
            "SUM(total_quantity)",
            ["total_quantity"],
            "订货订单商品件数",
            "order_source = 'PURCHASE'",
        ),
        field_metric(
            "总商品件数",
            "total_quantity",
            "SUM(total_quantity)",
            ["total_quantity"],
            "销售和订货商品总件数",
        ),
        field_metric(
            "销售GMV",
            "sale_gmv",
            "SUM(total_amount)",
            ["total_amount"],
            "销售订单金额",
            "order_source = 'SALE'",
        ),
        field_metric(
            "订货GMV",
            "purchase_gmv",
            "SUM(total_amount)",
            ["total_amount"],
            "订货订单金额",
            "order_source = 'PURCHASE'",
        ),
        field_metric(
            "总GMV",
            "total_gmv",
            "SUM(total_amount)",
            ["total_amount"],
            "销售和订货订单总金额",
        ),
        field_metric(
            "未发货订单数",
            "unshipped_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key"],
            "发货状态为0或1的订货订单数",
            "order_source = 'PURCHASE' AND ship_status IN (0, 1)",
        ),
        field_metric(
            "超时未发货订单数",
            "timeout_unshipped_count",
            "COUNT(DISTINCT order_key)",
            ["order_key", "order_date"],
            "超过15天仍未发货的订货订单数",
            "order_source = 'PURCHASE' AND ship_status IN (0, 1) AND DATEDIFF(CURRENT_DATE, order_date) > 15",
        ),
        field_metric(
            "销售下单客户数",
            "sale_customer_count",
            "COUNT(DISTINCT customer_id)",
            ["customer_id"],
            "销售订单去重客户数",
            "order_source = 'SALE'",
        ),
        field_metric(
            "订货下单客户数",
            "purchase_customer_count",
            "COUNT(DISTINCT customer_id)",
            ["customer_id"],
            "订货订单去重客户数",
            "order_source = 'PURCHASE'",
        ),
        field_metric(
            "总下单客户数",
            "total_customer_count",
            "COUNT(DISTINCT customer_id)",
            ["customer_id"],
            "销售和订货订单去重客户数",
        ),
        derived_metric(
            "销售客单价",
            "sale_avg_order_value",
            "sale_gmv / NULLIF(sale_order_count, 0)",
            ["sale_gmv", "sale_order_count"],
            "销售GMV除以销售订单数",
        ),
        derived_metric(
            "订货客单价",
            "purchase_avg_order_value",
            "purchase_gmv / NULLIF(purchase_order_count, 0)",
            ["purchase_gmv", "purchase_order_count"],
            "订货GMV除以订货订单数",
        ),
        derived_metric(
            "总客单价",
            "total_avg_order_value",
            "total_gmv / NULLIF(total_order_count, 0)",
            ["total_gmv", "total_order_count"],
            "总GMV除以总订单数",
        ),
        derived_metric(
            "销售客均成交额",
            "sale_avg_customer_value",
            "sale_gmv / NULLIF(sale_customer_count, 0)",
            ["sale_gmv", "sale_customer_count"],
            "销售GMV除以销售客户数",
        ),
        derived_metric(
            "订货客均成交额",
            "purchase_avg_customer_value",
            "purchase_gmv / NULLIF(purchase_customer_count, 0)",
            ["purchase_gmv", "purchase_customer_count"],
            "订货GMV除以订货客户数",
        ),
        derived_metric(
            "总客均成交额",
            "total_avg_customer_value",
            "total_gmv / NULLIF(total_customer_count, 0)",
            ["total_gmv", "total_customer_count"],
            "总GMV除以总客户数",
        ),
        field_metric(
            "应收总金额",
            "receivable_amount",
            "SUM(total_amount)",
            ["total_amount"],
            "销售订单应收总金额",
            "order_source = 'SALE'",
        ),
        field_metric(
            "已收金额",
            "received_amount",
            "SUM(received_amount)",
            ["received_amount"],
            "销售订单已收金额",
            "order_source = 'SALE'",
        ),
        field_metric(
            "未收金额",
            "unreceived_amount",
            "SUM(total_amount - received_amount)",
            ["total_amount", "received_amount"],
            "销售订单应收减已收",
            "order_source = 'SALE'",
        ),
        field_metric(
            "已结清订单数",
            "settled_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key", "total_amount", "received_amount"],
            "应收等于已收的销售订单数",
            "order_source = 'SALE' AND total_amount = received_amount",
        ),
        field_metric(
            "未结清订单数",
            "unsettled_order_count",
            "COUNT(DISTINCT order_key)",
            ["order_key", "total_amount", "received_amount"],
            "应收大于已收的销售订单数",
            "order_source = 'SALE' AND total_amount > received_amount",
        ),
        derived_metric(
            "回款率",
            "collection_rate",
            "received_amount / NULLIF(receivable_amount, 0)",
            ["received_amount", "receivable_amount"],
            "已收金额除以应收金额",
        ),
        field_metric(
            "客户欠款金额",
            "customer_debt_amount",
            "SUM(total_amount - received_amount)",
            ["total_amount", "received_amount"],
            "按客户查看销售订单未收金额",
            "order_source = 'SALE'",
        ),
        field_metric(
            "客户欠款笔数",
            "customer_debt_count",
            "COUNT(DISTINCT order_key)",
            ["order_key", "total_amount", "received_amount"],
            "按客户查看未结清销售订单数",
            "order_source = 'SALE' AND total_amount > received_amount",
        ),
    ]
    return {
        "track_event": track_event_metrics,
        "user_favorite": favorite_metrics,
        "traffic_activity": unified_traffic_metrics,
        "order_all": orders,
        "unshipped_order_detail": [
            field_metric(
                "未发订单明细金额",
                "unshipped_detail_amount",
                "SUM(total_price)",
                ["total_price"],
                "未发订单商品明细金额",
            ),
            field_metric(
                "订货件数",
                "ordered_quantity",
                "SUM(order_quantity)",
                ["order_quantity"],
                "未发订单订货件数",
            ),
            field_metric(
                "已发件数",
                "shipped_quantity",
                "SUM(shipped_quantity)",
                ["shipped_quantity"],
                "未发订单已发件数",
            ),
            field_metric(
                "未发件数",
                "unshipped_quantity",
                "SUM(unshipped_quantity)",
                ["unshipped_quantity"],
                "未发订单尚未发货件数",
            ),
        ],
        "product_inventory_analysis": [
            field_metric(
                "当前库存件数",
                "current_stock",
                "SUM(total_stock)",
                ["total_stock"],
                "当前商品库存合计",
            ),
            field_metric(
                "负库存数量",
                "negative_stock_quantity",
                "SUM(negative_stock_quantity)",
                ["negative_stock_quantity"],
                "负库存数量合计",
            ),
            field_metric(
                "负库存SKU数",
                "negative_stock_sku_count",
                "SUM(negative_sku_count)",
                ["negative_sku_count"],
                "库存小于0的SKU数",
            ),
            field_metric(
                "30天未动销商品数",
                "inactive_spu_count_30d",
                "SUM(inactive_spu_flag)",
                ["inactive_spu_flag"],
                "近30天销量为0的SPU数",
            ),
            field_metric(
                "近7天销量",
                "sales_quantity_7d",
                "SUM(quantity_7d)",
                ["quantity_7d"],
                "最近7天销售件数",
            ),
            field_metric(
                "近30天销量",
                "sales_quantity_30d",
                "SUM(quantity_30d)",
                ["quantity_30d"],
                "最近30天销售件数",
            ),
        ],
        "visitor_first_visit": [
            field_metric(
                "新增线上用户数",
                "new_online_user_count",
                "COUNT(DISTINCT user_id)",
                ["user_id"],
                "首次访问时间落在查询范围内的线上用户数",
            ),
        ],
        "seller_customer": [
            field_metric(
                "累计线下客户数",
                "offline_customer_count",
                "COUNT(DISTINCT id)",
                ["id"],
                "档口累计登记客户数",
            ),
            field_metric(
                "新增线下客户数",
                "new_offline_customer_count",
                "COUNT(DISTINCT id)",
                ["id"],
                "创建时间落在查询范围内的线下客户数",
            ),
        ],
        "customer_value": [
            field_metric(
                "客户消费金额",
                "customer_gmv",
                "SUM(customer_gmv)",
                ["customer_gmv"],
                "客户累计销售GMV",
            ),
            field_metric(
                "客户订单数",
                "customer_order_count",
                "SUM(customer_order_count)",
                ["customer_order_count"],
                "客户累计销售订单数",
            ),
            field_metric(
                "客户购买件数",
                "customer_quantity",
                "SUM(customer_quantity)",
                ["customer_quantity"],
                "客户累计购买件数",
            ),
            derived_metric(
                "客户客单价",
                "customer_avg_order_value",
                "customer_gmv / NULLIF(customer_order_count, 0)",
                ["customer_gmv", "customer_order_count"],
                "客户消费金额除以订单数",
            ),
            field_metric(
                "客户贡献率",
                "customer_contribution_rate",
                "MAX(contribution_rate)",
                ["contribution_rate"],
                "客户GMV占档口GMV的比例",
            ),
        ],
        "customer_recent_product": [
            field_metric(
                "最近购买商品件数",
                "recent_product_quantity",
                "SUM(quantity)",
                ["quantity"],
                "按客户和商品查看统计范围内购买件数",
            ),
            field_metric(
                "最近购买商品金额",
                "recent_product_amount",
                "SUM(total_price)",
                ["total_price"],
                "按客户和商品查看统计范围内购买金额",
            ),
        ],
    }


def ensure_metrics(api: FrontendApi, models: dict[str, dict[str, Any]]) -> int:
    created_or_updated = 0
    for model_biz_name, specs in metric_specs().items():
        model = models[model_biz_name]
        model_id = int(model["id"])
        existing = {
            item["biz_name"]: item
            for item in api.get("/semantic/metrics", model_id=model_id)
        }
        completed: dict[str, dict[str, Any]] = dict(existing)
        for spec in specs:
            payload = {
                "model_id": model_id,
                "name": spec["name"],
                "biz_name": spec["biz_name"],
                "description": spec["description"],
                "alias": [],
                "default_agg": spec["default_agg"],
                "define_type": spec["define_type"],
                "type_params": spec.get("type_params", {}),
            }
            if spec["define_type"] == "METRIC":
                refs = [completed[item] for item in spec["refs"]]
                payload["type_params"] = {
                    "metricDefineType": "METRIC",
                    "metricDefineByMetricParams": {
                        "expr": spec["expression"],
                        "filterSql": "",
                        "metrics": [
                            {
                                "id": item["id"],
                                "name": item["name"],
                                "bizName": item["biz_name"],
                            }
                            for item in refs
                        ],
                    },
                }
            if spec["biz_name"] in existing:
                result = api.put(
                    f"/semantic/metrics/{existing[spec['biz_name']]['id']}", payload
                )
            else:
                result = api.post("/semantic/metrics", payload)
            completed[spec["biz_name"]] = result
            created_or_updated += 1

        # 这些指标已经迁回物理模型，统一模型只保留跨表计算及其同模型依赖。
        if model_biz_name == "traffic_activity":
            desired = {spec["biz_name"] for spec in specs}
            moved_metrics = {
                "product_click_pv",
                "product_click_uv",
                "share_pv",
                "share_uv",
                "consult_uv",
            }
            for biz_name in moved_metrics - desired:
                metric = existing.get(biz_name)
                if metric is not None:
                    api.delete(f"/semantic/metrics/{metric['id']}")
    return created_or_updated


def ensure_dataset(
    api: FrontendApi, domain_id: int, models: dict[str, dict[str, Any]]
) -> int:
    payload = {
        "domain_id": domain_id,
        "name": "九表业务指标数据集",
        "biz_name": "semantic_business_dataset",
        "description": "通过前端可配置模型和指标组成的验证数据集",
        "alias": [],
        "data_set_detail": {
            "dataSetModelConfigs": [
                {
                    "id": int(model["id"]),
                    "includesAll": True,
                    "metrics": [],
                    "dimensions": [],
                }
                for model in models.values()
            ]
        },
        "query_config": {"semanticEnforcement": "LEGACY"},
    }
    existing = next(
        (
            item
            for item in api.get("/semantic/datasets", domain_id=domain_id)
            if item["biz_name"] == payload["biz_name"]
        ),
        None,
    )
    if existing:
        return int(api.put(f"/semantic/datasets/{existing['id']}", payload)["id"])
    return int(api.post("/semantic/datasets", payload)["id"])


def verify_assets(
    api: FrontendApi,
    datasource_id: int,
    domain_id: int,
    models: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """通过前端可调用的读取和执行接口校验已保存资产。"""
    del datasource_id
    connection = pymysql.connect(
        host="localhost",
        port=3306,
        user="root",
        password=MYSQL_PASSWORD,
        database="sqlbot_semantic_test",
    )
    try:
        with connection.cursor() as cursor:
            for spec in SQL_MODELS.values():
                sql = f"SELECT * FROM ({spec['sql']}) semantic_check LIMIT 0"
                cursor.execute(sql)
    finally:
        connection.close()

    metrics = [
        metric
        for model in models.values()
        for metric in api.get("/semantic/metrics", model_id=int(model["id"]))
    ]
    invalid = [
        metric for metric in metrics if metric.get("quality_status") == "INVALID"
    ]
    if invalid:
        details = [
            (metric["biz_name"], metric.get("quality_message")) for metric in invalid
        ]
        raise RuntimeError(f"INVALID_METRICS: {details}")
    datasets = api.get("/semantic/datasets", domain_id=domain_id)
    return {
        "sql_model_count": len(SQL_MODELS),
        "stored_metric_count": len(metrics),
        "invalid_metric_count": len(invalid),
        "dataset_count": len(datasets),
    }


def main() -> None:
    api = FrontendApi()
    datasource_id = ensure_datasource(api)
    domain_id = ensure_domain(api)
    models = ensure_models(api, datasource_id, domain_id)
    metric_count = ensure_metrics(api, models)
    dataset_id = ensure_dataset(api, domain_id, models)
    verification = verify_assets(api, datasource_id, domain_id, models)
    print(
        {
            "datasource_id": datasource_id,
            "domain_id": domain_id,
            "model_count": len(models),
            "metric_count": metric_count,
            "dataset_id": dataset_id,
            "verification": verification,
        }
    )


if __name__ == "__main__":
    main()
