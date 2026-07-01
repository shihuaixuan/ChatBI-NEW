"""通过 Graph API 评估 ChatBI 首期 20 个单表问题。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
import requests
from sqlmodel import Session, select

from apps.datasource.models.datasource import CoreDatasource
from apps.datasource.utils.utils import aes_decrypt
from apps.system.models.user import UserModel
from common.core.db import engine
from common.core.security import create_access_token

API_BASE = "http://127.0.0.1:8000/api/v1"
DATASET_ID = 243
DATASOURCE_ID = 13
OUTPUT_JSON = Path("../docs/tech/chatbi_first_phase_20q_evaluation.json")
OUTPUT_MD = Path("../docs/tech/chatbi_first_phase_20q_evaluation.md")


@dataclass(frozen=True)
class EvalCase:
    number: int
    question: str
    expected_sql: str
    expected_table: str
    expected_metrics: tuple[str, ...]
    expected_dimensions: tuple[str, ...] = ()
    expected_filters: tuple[str, ...] = ()
    expected_intent_types: tuple[str, ...] = ("metric_query",)
    expected_metric_labels: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class EvalResult:
    case: EvalCase
    expected_rows: list[dict[str, Any]] = field(default_factory=list)
    api_status: str = ""
    run_id: str = ""
    intent: dict[str, Any] = field(default_factory=dict)
    interactions: list[dict[str, Any]] = field(default_factory=list)
    knowledge: dict[str, Any] = field(default_factory=dict)
    generated_sql: str = ""
    generated_rows: list[dict[str, Any]] = field(default_factory=list)
    sql_execution: dict[str, Any] = field(default_factory=dict)
    final_answer: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


CASES = [
    EvalCase(
        1,
        "2026 年 6 月各档口的总 GMV 分别是多少？",
        """
        SELECT stall_id, ROUND(SUM(gmv_total), 2) AS gmv_total
        FROM fct_stall_order_daily
        WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01'
        GROUP BY stall_id ORDER BY stall_id
        """,
        "fct_stall_order_daily",
        ("gmv_total",),
        ("stall_id",),
        ("stat_date",),
        ("metric_query",),
        ("总GMV",),
    ),
    EvalCase(
        2,
        "最近 30 天每天的总订单数和总 GMV 趋势如何？",
        """
        SELECT stat_date, SUM(order_cnt_total) AS order_cnt_total,
               ROUND(SUM(gmv_total), 2) AS gmv_total
        FROM fct_stall_order_daily
        WHERE stat_date >= '2026-06-01' AND stat_date <= '2026-06-30'
        GROUP BY stat_date ORDER BY stat_date
        """,
        "fct_stall_order_daily",
        ("order_cnt_total", "gmv_total"),
        ("stat_date",),
        ("stat_date",),
        ("trend_analysis",),
        ("总订单数", "总GMV"),
        "一次查询需要返回两个指标。",
    ),
    EvalCase(
        3,
        "2026 年 6 月销售 GMV 最高的 5 个档口是哪些？",
        """
        SELECT stall_id, ROUND(SUM(gmv_sale), 2) AS gmv_sale
        FROM fct_stall_order_daily
        WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01'
        GROUP BY stall_id ORDER BY gmv_sale DESC LIMIT 5
        """,
        "fct_stall_order_daily",
        ("gmv_sale",),
        ("stall_id",),
        ("stat_date",),
        ("ranking_analysis",),
        ("销售类GMV",),
    ),
    EvalCase(
        4,
        "各档口的销售订单平均客单价分别是多少？",
        """
        SELECT stall_id,
               ROUND(SUM(gmv_sale) / NULLIF(SUM(order_cnt_sale), 0), 2) AS aov_sale
        FROM fct_stall_order_daily
        GROUP BY stall_id ORDER BY stall_id
        """,
        "fct_stall_order_daily",
        ("aov_sale",),
        ("stall_id",),
        (),
        ("metric_query",),
        ("销售订单平均客单价",),
    ),
    EvalCase(
        5,
        "当前共有多少笔未发订单？",
        "SELECT COUNT(DISTINCT order_no) AS unshipped_order_cnt FROM snap_unshipped_order",
        "snap_unshipped_order",
        ("unshipped_order_cnt",),
        (),
        (),
        ("metric_query",),
        ("未发订单数",),
    ),
    EvalCase(
        6,
        "当前超时未发订单有多少笔？",
        """
        SELECT COUNT(DISTINCT order_no) AS overtime_order_cnt
        FROM snap_unshipped_order WHERE is_overtime = 1
        """,
        "snap_unshipped_order",
        ("overtime_order_cnt",),
        (),
        ("is_overtime",),
        ("metric_query",),
        ("超时订单数",),
    ),
    EvalCase(
        7,
        "超时天数最长的 10 笔订单是哪些？",
        """
        SELECT order_no, seller_id, stall_id, customer_name, overtime_days,
               unshipped_qty, order_amount
        FROM snap_unshipped_order
        WHERE is_overtime = 1
        ORDER BY overtime_days DESC, order_no
        LIMIT 10
        """,
        "snap_unshipped_order",
        ("overtime_days",),
        ("order_no",),
        ("is_overtime",),
        ("ranking_analysis", "detail_query"),
        ("超时天数",),
    ),
    EvalCase(
        8,
        "各档口当前的未发件数和未发订单金额分别是多少？",
        """
        SELECT stall_id, SUM(unshipped_qty) AS unshipped_qty,
               ROUND(SUM(order_amount), 2) AS order_amount
        FROM snap_unshipped_order
        GROUP BY stall_id ORDER BY stall_id
        """,
        "snap_unshipped_order",
        ("unshipped_qty", "order_amount"),
        ("stall_id",),
        (),
        ("metric_query",),
        ("未发件数", "订单金额"),
        "一次查询需要返回两个指标。",
    ),
    EvalCase(
        9,
        "当前各档口的库存总量是多少？",
        """
        SELECT stall_id, SUM(stock_qty) AS stock_qty
        FROM snap_product_inventory
        GROUP BY stall_id ORDER BY stall_id
        """,
        "snap_product_inventory",
        ("stock_qty",),
        ("stall_id",),
        (),
        ("metric_query",),
        ("当前库存件数",),
    ),
    EvalCase(
        10,
        "当前有哪些商品处于负库存状态？",
        """
        SELECT product_id, goods_no, product_name, seller_id, stall_id, stock_qty
        FROM snap_product_inventory
        WHERE stock_qty < 0
        ORDER BY stock_qty, product_id
        """,
        "snap_product_inventory",
        ("stock_qty",),
        ("product_id",),
        ("stock_qty",),
        ("detail_query",),
        ("当前库存件数",),
    ),
    EvalCase(
        11,
        "连续 30 天未动销的商品有多少个？",
        """
        SELECT COUNT(DISTINCT product_id) AS dormant_product_cnt
        FROM snap_product_inventory WHERE days_unsold >= 30
        """,
        "snap_product_inventory",
        ("dormant_product_cnt",),
        (),
        ("days_unsold",),
        ("metric_query",),
        ("滞销商品数",),
    ),
    EvalCase(
        12,
        "当前库存量最低的 10 个商品是哪些？",
        """
        SELECT product_id, goods_no, product_name, seller_id, stall_id, stock_qty
        FROM snap_product_inventory
        ORDER BY stock_qty, product_id LIMIT 10
        """,
        "snap_product_inventory",
        ("stock_qty",),
        ("product_id",),
        (),
        ("ranking_analysis",),
        ("当前库存件数",),
    ),
    EvalCase(
        13,
        "2026 年 6 月消费金额最高的 10 位客户是谁？",
        """
        SELECT customer_id, MAX(customer_name) AS customer_name,
               ROUND(SUM(customer_gmv), 2) AS customer_gmv
        FROM fct_customer_trade_daily
        WHERE stat_date >= '2026-06-01' AND stat_date < '2026-07-01'
        GROUP BY customer_id ORDER BY customer_gmv DESC, customer_id LIMIT 10
        """,
        "fct_customer_trade_daily",
        ("customer_gmv",),
        ("customer_id",),
        ("stat_date",),
        ("ranking_analysis",),
        ("客户当日GMV",),
    ),
    EvalCase(
        14,
        "最近 30 天每天的客户 GMV 趋势如何？",
        """
        SELECT stat_date, ROUND(SUM(customer_gmv), 2) AS customer_gmv
        FROM fct_customer_trade_daily
        WHERE stat_date >= '2026-06-01' AND stat_date <= '2026-06-30'
        GROUP BY stat_date ORDER BY stat_date
        """,
        "fct_customer_trade_daily",
        ("customer_gmv",),
        ("stat_date",),
        ("stat_date",),
        ("trend_analysis",),
        ("客户当日GMV",),
    ),
    EvalCase(
        15,
        "各档口的新增成交客户数分别是多少？",
        """
        SELECT stall_id, COUNT(DISTINCT customer_id) AS new_customer_cnt
        FROM fct_customer_trade_daily
        WHERE is_first_deal = 1
        GROUP BY stall_id ORDER BY stall_id
        """,
        "fct_customer_trade_daily",
        ("new_customer_cnt",),
        ("stall_id",),
        ("is_first_deal",),
        ("metric_query",),
        ("新增成交客户数",),
    ),
    EvalCase(
        16,
        "线上和线下渠道的 GMV、订单数分别是多少？",
        """
        SELECT channel_type, ROUND(SUM(customer_gmv), 2) AS customer_gmv,
               SUM(order_cnt) AS order_cnt
        FROM fct_customer_trade_daily
        GROUP BY channel_type ORDER BY channel_type
        """,
        "fct_customer_trade_daily",
        ("customer_gmv", "order_cnt"),
        ("channel_type",),
        (),
        ("comparison_analysis", "metric_query"),
        ("客户当日GMV", "客户当日订单数"),
        "一次查询需要返回两个指标。",
    ),
    EvalCase(
        17,
        "当前客户欠款总金额是多少？",
        "SELECT ROUND(SUM(arrears_amt), 2) AS arrears_amt FROM snap_customer_arrears",
        "snap_customer_arrears",
        ("arrears_amt",),
        (),
        (),
        ("metric_query",),
        ("当前欠款余额",),
    ),
    EvalCase(
        18,
        "当前有多少位客户已经逾期？",
        """
        SELECT COUNT(DISTINCT customer_id) AS overdue_customer_cnt
        FROM snap_customer_arrears WHERE is_overdue = 1
        """,
        "snap_customer_arrears",
        ("overdue_customer_cnt",),
        (),
        ("is_overdue",),
        ("metric_query",),
        ("逾期客户数",),
    ),
    EvalCase(
        19,
        "欠款金额最高的 10 位客户是谁？",
        """
        SELECT customer_id, customer_name, arrears_amt
        FROM snap_customer_arrears
        ORDER BY arrears_amt DESC, customer_id LIMIT 10
        """,
        "snap_customer_arrears",
        ("arrears_amt",),
        ("customer_id",),
        (),
        ("ranking_analysis",),
        ("当前欠款余额",),
    ),
    EvalCase(
        20,
        "各档口的欠款金额、逾期金额和欠款客户数分别是多少？",
        """
        SELECT stall_id, ROUND(SUM(arrears_amt), 2) AS arrears_amt,
               ROUND(SUM(overdue_amt), 2) AS overdue_amt,
               COUNT(DISTINCT customer_id) AS arrears_customer_cnt
        FROM snap_customer_arrears
        GROUP BY stall_id ORDER BY stall_id
        """,
        "snap_customer_arrears",
        ("arrears_amt", "overdue_amt", "arrears_customer_cnt"),
        ("stall_id",),
        (),
        ("metric_query",),
        ("当前欠款余额", "逾期欠款金额", "欠款客户数"),
        "一次查询需要返回三个指标。",
    ),
]


def load_local_context() -> tuple[dict[str, str], dict[str, Any]]:
    """生成测试令牌，并读取外部业务库连接配置。"""

    with Session(engine) as session:
        user = session.exec(select(UserModel).where(UserModel.status == 1).order_by(UserModel.id)).first()
        datasource = session.get(CoreDatasource, DATASOURCE_ID)
        if user is None or datasource is None:
            raise RuntimeError("缺少测试用户或目标数据源")
        token = create_access_token(
            {"id": user.id, "account": user.account, "oid": user.oid},
            expires_delta=timedelta(hours=6),
        )
        datasource_config = json.loads(aes_decrypt(datasource.configuration))
    return {"X-SQLBOT-TOKEN": f"Bearer {token}"}, datasource_config


def mysql_connection(config: dict[str, Any]):
    return pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["username"],
        password=config["password"],
        database=config["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def execute_rows(connection, sql: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [_json_row(row) for row in cursor.fetchall()]


def unwrap_response(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def run_api_case(
    session: requests.Session,
    headers: dict[str, str],
    case: EvalCase,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_id = f"eval-20q-{case.number:02d}-{int(time.time())}"
    response = session.post(
        f"{API_BASE}/graph/queries",
        headers=headers,
        json={
            "question": case.question,
            "dataset_id": DATASET_ID,
            "definition_version": "v1",
            "run_id": run_id,
        },
        timeout=240,
    )
    run = unwrap_response(response)
    interactions: list[dict[str, Any]] = []

    for _ in range(4):
        if run.get("status") != "waiting_input":
            break
        pending = (run.get("context_summary") or {}).get("pending_interaction") or {}
        selected_response = choose_interaction_response(pending, case)
        interactions.append(
            {
                "node_name": pending.get("node_name"),
                "prompt": pending.get("prompt"),
                "options": pending.get("options") or [],
                "selected_response": selected_response,
            }
        )
        response = session.post(
            f"{API_BASE}/graph/runs/{run_id}/interactions/{pending['interaction_id']}/responses",
            headers=headers,
            json={"response": selected_response},
            timeout=240,
        )
        unwrap_response(response)
        run = unwrap_response(
            session.get(f"{API_BASE}/graph/runs/{run_id}", headers=headers, timeout=60)
        )

    return run, interactions


def choose_interaction_response(pending: dict[str, Any], case: EvalCase) -> dict[str, Any]:
    """根据基准指标和预期分析形态自动回答测试中的澄清卡片。"""

    options = pending.get("options") or []
    node_name = str(pending.get("node_name") or "")

    if node_name == "ask_metric_selection":
        for label in case.expected_metric_labels:
            for option in options:
                if label and label in str(option.get("label") or ""):
                    return {"metric": str(option.get("value"))}
        if options:
            return {"metric": str(options[0].get("value"))}
        return {"skipped": True}

    if node_name == "ask_intent_clarification":
        preferred = {
            "trend_analysis": "看趋势",
            "ranking_analysis": "看排名",
            "comparison_analysis": "看对比",
            "detail_query": "看明细",
            "metric_query": "查指标数值",
        }
        labels = [preferred[item] for item in case.expected_intent_types if item in preferred]
        for label in labels:
            for option in options:
                if option.get("label") == label and isinstance(option.get("value"), dict):
                    return option["value"]

    if options:
        value = options[0].get("value")
        if isinstance(value, dict):
            return value
        properties = (pending.get("response_schema") or {}).get("properties") or {}
        key = next((name for name in properties if name != "skipped"), "value")
        return {key: str(value)}
    return {"skipped": True}


def assess_case(result: EvalResult) -> None:
    case = result.case
    sql_text = result.generated_sql.lower()
    intent_type = str(result.intent.get("intent_type") or "")
    knowledge_metrics = set(result.knowledge.get("metrics") or [])
    knowledge_dimensions = set(result.knowledge.get("dimensions") or [])
    filters = ((result.knowledge.get("slot_bindings") or {}).get("filters") or [])

    result.checks = {
        "api_succeeded": result.api_status == "succeeded",
        "intent_type_correct": intent_type in case.expected_intent_types,
        "metric_binding_complete": set(case.expected_metrics).issubset(knowledge_metrics)
        or all(metric.lower() in sql_text for metric in case.expected_metrics),
        "dimension_binding_complete": set(case.expected_dimensions).issubset(knowledge_dimensions)
        or all(dimension.lower() in sql_text for dimension in case.expected_dimensions),
        "table_correct": case.expected_table.lower() in sql_text,
        "time_or_filter_present": not case.expected_filters
        or bool(filters)
        or all(field.lower() in sql_text and _sql_has_filter_for(sql_text, field) for field in case.expected_filters),
        "result_equal": _rows_equal(result.expected_rows, result.generated_rows),
    }
    result.checks["overall"] = all(result.checks.values())


def _sql_has_filter_for(sql_text: str, field: str) -> bool:
    where_match = re.search(r"\bwhere\b(.+?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)", sql_text, re.S)
    return bool(where_match and field.lower() in where_match.group(1))


def _rows_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return _canonical_rows(left) == _canonical_rows(right)


def _canonical_rows(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(_json_row(row), ensure_ascii=False, sort_keys=True) for row in rows)


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_scalar(value) for key, value in row.items()}


def _json_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    return value


def _mark(checks: dict[str, bool], key: str) -> str:
    return "✅" if checks.get(key) else "❌"


def result_to_dict(result: EvalResult) -> dict[str, Any]:
    return {
        "number": result.case.number,
        "question": result.case.question,
        "expected": {
            "sql": " ".join(result.case.expected_sql.split()),
            "intent_types": list(result.case.expected_intent_types),
            "table": result.case.expected_table,
            "metrics": list(result.case.expected_metrics),
            "dimensions": list(result.case.expected_dimensions),
            "filters": list(result.case.expected_filters),
            "rows": result.expected_rows,
            "notes": result.case.notes,
        },
        "actual": {
            "run_id": result.run_id,
            "status": result.api_status,
            "intent": result.intent,
            "interactions": result.interactions,
            "knowledge": {
                "status": result.knowledge.get("status"),
                "metrics": result.knowledge.get("metrics"),
                "dimensions": result.knowledge.get("dimensions"),
                "slot_bindings": result.knowledge.get("slot_bindings"),
                "ambiguities": result.knowledge.get("ambiguities"),
            },
            "sql": result.generated_sql,
            "sql_execution": result.sql_execution,
            "rows": result.generated_rows,
            "final_answer": result.final_answer,
            "trace": result.trace,
        },
        "checks": result.checks,
        "errors": result.errors,
    }


def write_outputs(results: list[EvalResult]) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = [result_to_dict(result) for result in results]
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = sum(1 for result in results if result.checks.get("overall"))
    lines = [
        "# ChatBI 首期 20 问 API 评估报告",
        "",
        f"- 数据集：`{DATASET_ID}`",
        f"- 完成：{len(results)}/20",
        f"- 全链路通过：{passed}/{len(results)}",
        "",
        "## 汇总",
        "",
        "| # | 问题 | API | 意图 | 指标 | 维度 | 过滤 | 结果 | 总体 |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        checks = result.checks
        lines.append(
            f"| {result.case.number} | {result.case.question} | {_mark(checks, 'api_succeeded')} | "
            f"{_mark(checks, 'intent_type_correct')} | {_mark(checks, 'metric_binding_complete')} | "
            f"{_mark(checks, 'dimension_binding_complete')} | {_mark(checks, 'time_or_filter_present')} | "
            f"{_mark(checks, 'result_equal')} | {_mark(checks, 'overall')} |"
        )

    for result in results:
        lines.extend(
            [
                "",
                f"## {result.case.number}. {result.case.question}",
                "",
                f"- 状态：`{result.api_status}`",
                f"- Run ID：`{result.run_id}`",
                f"- 预期意图：`{', '.join(result.case.expected_intent_types)}`",
                f"- 实际意图：`{result.intent.get('intent_type')}`",
                f"- 交互次数：{len(result.interactions)}",
                f"- 预期 SQL：`{' '.join(result.case.expected_sql.split())}`",
                f"- 实际 SQL：`{result.generated_sql}`",
                f"- 预期结果：`{json.dumps(result.expected_rows, ensure_ascii=False)}`",
                f"- 实际结果：`{json.dumps(result.generated_rows, ensure_ascii=False)}`",
                f"- 检查：`{json.dumps(result.checks, ensure_ascii=False)}`",
            ]
        )
        if result.interactions:
            lines.append(f"- 交互：`{json.dumps(result.interactions, ensure_ascii=False)}`")
        if result.errors:
            lines.append(f"- 错误：`{json.dumps(result.errors, ensure_ascii=False)}`")
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    headers, datasource_config = load_local_context()
    http = requests.Session()
    results: list[EvalResult] = []

    with mysql_connection(datasource_config) as connection:
        for case in CASES:
            print(f"[{case.number:02d}/20] 基准查询：{case.question}", flush=True)
            result = EvalResult(case=case)
            try:
                result.expected_rows = execute_rows(connection, case.expected_sql)
                run, interactions = run_api_case(http, headers, case)
                variables = ((run.get("context_summary") or {}).get("variables") or {})
                result.api_status = str(run.get("status") or "")
                result.run_id = str(run.get("run_id") or "")
                result.intent = variables.get("intent") or {}
                result.interactions = interactions
                result.knowledge = variables.get("knowledge") or {}
                result.generated_sql = str((variables.get("sql") or {}).get("sql") or "")
                result.sql_execution = variables.get("sql_execution") or {}
                result.final_answer = variables.get("answer") or {}
                if result.generated_sql:
                    result.generated_rows = execute_rows(connection, result.generated_sql)
                result.trace = unwrap_response(
                    http.get(
                        f"{API_BASE}/graph/runs/{result.run_id}/trace",
                        headers=headers,
                        timeout=60,
                    )
                )
                assess_case(result)
            except Exception as exc:
                result.errors.append(f"{type(exc).__name__}: {exc}")
                result.checks = {"overall": False}
            results.append(result)
            write_outputs(results)
            print(
                f"[{case.number:02d}/20] status={result.api_status or 'error'} "
                f"overall={result.checks.get('overall')} interactions={len(result.interactions)}",
                flush=True,
            )

    write_outputs(results)
    passed = sum(1 for result in results if result.checks.get("overall"))
    print(f"完成：{passed}/20 全链路通过", flush=True)
    print(f"JSON：{OUTPUT_JSON.resolve()}", flush=True)
    print(f"Markdown：{OUTPUT_MD.resolve()}", flush=True)


if __name__ == "__main__":
    main()
