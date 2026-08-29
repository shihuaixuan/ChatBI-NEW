"""使用相关语义资产 YAML 上下文，直接测试模型生成 SQL 的能力。

脚本默认复用 ``evaluate_chatbi_first_phase.py`` 中的两个真实问题。
它不经过 Graph、Agent、问题理解、语义检索或 Semantic SQL Compiler，而是：

1. 根据真实问题对应的测试标注，从已发布的 dataset schema 选择相关语义资产；
2. 将相关资产投影为最新设计使用的 YAML 文档，并和问题一起发送给当前默认模型；
3. 解析模型返回的 SQL；
4. 使用真实问题对应的期望 SQL 做结构性检查。

示例：

    cd backend
    .venv/bin/python scripts/test_direct_model_sql_generation.py --case 1
    .venv/bin/python scripts/test_direct_model_sql_generation.py

若只想检查上下文和提示词是否能够构造，可加 ``--dry-run``，不会调用模型。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import sqlglot
import yaml
from sqlglot import exp
from sqlmodel import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from evaluate_chatbi_first_phase import CASES  # noqa: E402

from apps.ai_model.runtime import build_llm_runtime  # noqa: E402
from apps.chatbi.adapters.langchain import LangChainGenerationModelClient  # noqa: E402
from apps.chatbi.models import ModelMessage  # noqa: E402
from apps.chatbi.models.dto.research_agent import (  # noqa: E402
    SemanticContext,
    SemanticDimension,
    SemanticMetric,
)
from apps.chatbi.services.generation.sql_generation import (  # noqa: E402
    SQLGenerationError,
    parse_sql_generation_result,
)
from apps.chatbi.services.research.agent_context import (  # noqa: E402
    serialize_semantic_context_yaml,
)
from apps.semantic.composition import build_semantic_schema_service  # noqa: E402
from common.core.db import engine  # noqa: E402

DEFAULT_DATASET_ID = 243
DEFAULT_TENANT_ID = 1
DEFAULT_OUTPUT = BACKEND_ROOT / "data" / "direct_model_sql_generation.json"
DEFAULT_CONTEXT_OUTPUT = (
    BACKEND_ROOT / "data" / "direct_model_sql_generation.semantic_context.yaml"
)
DEFAULT_CASE_NUMBERS = (1, 5)

SYSTEM_PROMPT = """你是一个只负责生成查询 SQL 的数据分析模型。

你的输入包含一个用户问题和当前问题相关的语义资产 YAML 文档。语义资产是唯一可信来源，
其中的模型、表、字段、指标定义、指标过滤条件、时间语义和维度定义必须优先于你的
常识。你只需要生成 SQL，不需要执行 SQL，也不需要解释推理过程。

必须遵守以下规则：
1. 只能生成只读查询；只能返回 SELECT 或 WITH 开头的查询，禁止 INSERT、UPDATE、DELETE、DDL。
2. 只能使用语义资产中定义的模型 tableQuery/sqlQuery、字段和指标；不得编造表名或字段名。
3. 指标必须按照语义资产中的定义生成。CUSTOM 指标直接使用其完整表达式，不要再次套用错误的聚合。
4. 指标所属模型决定查询表；不同模型的指标不能无依据地拼接到同一张表。
5. 用户明确给出时间时，使用对应模型的默认时间维度；日期范围使用左闭右开条件。
6. “当前”只在语义资产的快照指标上使用当前快照语义；不要擅自把快照指标改成事件时间查询。
7. 用户明确要求 TopN、前 N、最高 N 个或最低 N 个时，使用对应排序和 LIMIT；没有明确要求时不要擅自添加 LIMIT。
8. 用户明确要求分组、趋势、排名或明细时，正确生成 GROUP BY、ORDER BY 和明细字段。
9. 保留语义资产中的英文表名和字段名原样；函数表达式和结果列必须有清晰别名。
10. 不要把期望答案、测试说明或本规则写入 SQL。

只输出一个严格 JSON 对象，不要输出 Markdown 代码块或额外文字。
成功格式：{"success":true,"sql":"...","tables":["..."],"chart-type":"table","brief":"..."}
无法安全生成时：{"success":false,"message":"..."}
"""


def load_semantic_schema(tenant_id: int, dataset_id: int) -> tuple[Any, dict[str, Any]]:
    """读取已发布的 DatasetSchema，具体上下文由每道题单独裁剪。"""

    with Session(engine) as session:
        schema = build_semantic_schema_service(session).build_dataset_schema(
            tenant_id,
            dataset_id,
        )
    metadata = {
        "dataset_id": dataset_id,
        "schema_version": schema.schema_version,
        "contract_version": schema.contract_version,
        "schema_fingerprint": schema.schema_fingerprint,
        "published_model_count": len(schema.models),
        "published_metric_count": len(schema.metrics),
        "published_dimension_count": len(schema.dimensions),
    }
    return schema, metadata


def _normalized(value: Any) -> str:
    """归一化业务名称，便于从测试标注选择已发布资产。"""

    return "".join(str(value or "").casefold().split())


def _model_for_case(case: Any, schema: Any) -> dict[str, Any]:
    """按真实问题的期望表定位语义模型，不把期望 SQL放入模型输入。"""

    expected_table = _normalized(case.expected_table)
    for model in schema.models:
        if _normalized(model.get("tableQuery")) == expected_table:
            return model
    raise ValueError(
        f"测试题 {case.number} 的期望表不在已发布语义模型中：{case.expected_table}"
    )


def _metric_for_case(case: Any, schema: Any, model_id: int) -> list[Any]:
    """选择该题实际使用的指标资产。"""

    expected = {_normalized(item) for item in case.expected_metrics}
    return [
        metric
        for metric in schema.metrics
        if metric.model == model_id and _normalized(metric.biz_name) in expected
    ]


def _dimension_for_name(name: str, schema: Any, model_id: int) -> Any | None:
    """按业务名选择指定模型中的维度资产。"""

    target = _normalized(name)
    for dimension in schema.dimensions:
        if dimension.model != model_id:
            continue
        names = (dimension.biz_name, dimension.name, *(dimension.alias or []))
        if target in {_normalized(item) for item in names}:
            return dimension
    return None


def _metric_expression(metric: Any) -> str:
    """读取指标定义中的可执行表达式，保留自定义指标完整口径。"""

    params = metric.type_params.get("metricDefineByMeasureParams", {})
    expression = params.get("expr") if isinstance(params, dict) else None
    return str(expression or metric.description or metric.biz_name)


def build_semantic_yaml(case: Any, schema: Any) -> tuple[str, dict[str, Any]]:
    """将当前题目相关资产投影为有界 YAML 文档。"""

    model = _model_for_case(case, schema)
    model_id = int(model["id"])
    metrics = _metric_for_case(case, schema, model_id)
    if len(metrics) != len(case.expected_metrics):
        raise ValueError(f"测试题 {case.number} 的指标无法完整绑定到语义资产")

    dimension_names = [*case.expected_dimensions, *case.expected_filters]
    dimensions: list[Any] = []
    for name in dimension_names:
        dimension = _dimension_for_name(name, schema, model_id)
        if dimension is not None and dimension.id not in {
            item.id for item in dimensions
        }:
            dimensions.append(dimension)
    default_time_field = str(model.get("default_time_field") or "")
    if default_time_field:
        time_dimension = _dimension_for_name(default_time_field, schema, model_id)
        if time_dimension is not None and time_dimension.id not in {
            item.id for item in dimensions
        }:
            dimensions.append(time_dimension)

    semantic_context = SemanticContext(
        metrics=tuple(
            SemanticMetric(
                ref=f"METRIC:{metric.id}:{metric.model}",
                name=metric.name,
                description=metric.description or "",
                aggregation=metric.default_agg or "SUM",
                dimensions=tuple(
                    f"DIMENSION:{dimension.id}:{dimension.model}"
                    for dimension in dimensions
                ),
            )
            for metric in metrics
        ),
        dimensions=tuple(
            SemanticDimension(
                ref=f"DIMENSION:{dimension.id}:{dimension.model}",
                name=dimension.name,
                description=dimension.description or "",
                grains=tuple(dimension.ext_info.get("time_granularities") or []),
            )
            for dimension in dimensions
        ),
    )
    # 复用最新 Research 设计的固定字段顺序和长度边界，再补充 SQL 所需的物理映射。
    semantic_document = yaml.safe_load(
        serialize_semantic_context_yaml(semantic_context)
    )
    semantic_document["dataset"] = {
        "id": schema.data_set.id,
        "name": schema.data_set.name,
        "database_type": schema.database_type or "mysql",
    }
    semantic_document["sql_sources"] = [
        {
            "model_ref": f"MODEL:{model_id}",
            "name": model.get("name"),
            "biz_name": model.get("biz_name"),
            "table": model.get("tableQuery"),
            "default_time_field": model.get("default_time_field"),
            "fields": [
                {
                    "name": field.get("fieldName") or field.get("bizName"),
                    "data_type": field.get("dataType"),
                    "semantic_type": field.get("semanticType"),
                }
                for field in model.get("fields") or []
                if (field.get("fieldName") or field.get("bizName"))
                in {
                    *{field_name for metric in metrics for field_name in metric.fields},
                    *(dimension.biz_name for dimension in dimensions),
                }
            ],
        }
    ]
    semantic_document["metric_expressions"] = [
        {
            "ref": f"METRIC:{metric.id}:{metric.model}",
            "biz_name": metric.biz_name,
            "name": metric.name,
            "expression": _metric_expression(metric),
            "time_semantics": metric.ext_info.get("time_semantics"),
            "snapshot_aggregation": metric.ext_info.get("snapshot_aggregation"),
            "filter_sql": metric.ext_info.get("filter_sql"),
        }
        for metric in metrics
    ]
    yaml_context = yaml.safe_dump(
        semantic_document,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )
    context_metadata = {
        "model_ref": f"MODEL:{model_id}",
        "table": model.get("tableQuery"),
        "metric_refs": [f"METRIC:{metric.id}:{metric.model}" for metric in metrics],
        "dimension_refs": [
            f"DIMENSION:{dimension.id}:{dimension.model}" for dimension in dimensions
        ],
        "chars": len(yaml_context),
        "sha256": hashlib.sha256(yaml_context.encode("utf-8")).hexdigest(),
    }
    return yaml_context, context_metadata


def build_messages(question: str, semantic_context: str) -> list[ModelMessage]:
    """构造只包含规则、问题和语义资产的直接模型输入。"""

    user_prompt = (
        "<user-question>\n"
        f"{question}\n"
        "</user-question>\n\n"
        "<semantic-assets>\n"
        f"{semantic_context}\n"
        "</semantic-assets>"
    )
    return [
        ModelMessage(role="system", content=SYSTEM_PROMPT, system_context=True),
        ModelMessage(role="human", content=user_prompt, system_context=True),
    ]


def _normalize_sql_text(value: str) -> str:
    """归一化 SQL 文本，仅用于不区分大小写的测试断言。"""

    return re.sub(r"\s+", " ", value.replace("`", "")).strip().casefold()


def _expected_limit(expected_sql: str) -> int | None:
    match = re.search(r"\blimit\s+(\d+)\b", expected_sql, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _actual_limit(sql: str) -> int | None:
    match = re.search(r"\blimit\s+(\d+)\b", sql, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _tables_from_sql(sql: str) -> set[str]:
    """使用 SQL AST 提取表名，避免仅靠字符串误判字段名。"""

    tree = sqlglot.parse_one(sql, read="mysql")
    return {table.name.casefold() for table in tree.find_all(exp.Table) if table.name}


def evaluate_sql(case: Any, sql: str, tables: list[str] | None) -> dict[str, Any]:
    """对模型结果做与具体题目语义相关的结构性检查。"""

    normalized = _normalize_sql_text(sql)
    checks: dict[str, bool] = {
        "non_empty": bool(sql.strip()),
        "read_only": bool(re.match(r"^(select|with)\b", sql.strip(), re.IGNORECASE)),
    }
    actual_tables: list[str] = []
    syntax_error = ""
    try:
        actual_tables = sorted(_tables_from_sql(sql))
        checks["mysql_syntax"] = True
    except (sqlglot.errors.ParseError, ValueError) as exc:
        syntax_error = str(exc)
        checks["mysql_syntax"] = False

    expected_table = case.expected_table.casefold()
    checks["expected_table"] = expected_table in actual_tables
    checks["expected_metrics"] = all(
        metric.casefold() in normalized for metric in case.expected_metrics
    )
    checks["expected_dimensions"] = all(
        dimension.casefold() in normalized for dimension in case.expected_dimensions
    )
    checks["expected_filters"] = all(
        filter_name.casefold() in normalized for filter_name in case.expected_filters
    )

    expected_limit = _expected_limit(case.expected_sql)
    actual_limit = _actual_limit(sql)
    checks["limit_shape"] = (
        actual_limit == expected_limit
        if expected_limit is not None
        else actual_limit is None
    )
    if tables:
        checks["reported_tables"] = expected_table in {
            str(table).casefold() for table in tables
        }

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "actual_tables": actual_tables,
        "reported_tables": tables,
        "expected_table": case.expected_table,
        "expected_metrics": list(case.expected_metrics),
        "expected_dimensions": list(case.expected_dimensions),
        "expected_filters": list(case.expected_filters),
        "expected_limit": expected_limit,
        "actual_limit": actual_limit,
        "syntax_error": syntax_error,
    }


def _parse_case_selection(raw_values: list[str], total: int) -> list[int]:
    """解析可重复的 --case 参数，返回去重后的 1-based 题号。"""

    if not raw_values:
        return [number for number in DEFAULT_CASE_NUMBERS if number <= total]
    selected: list[int] = []
    for raw in raw_values:
        for item in raw.split(","):
            value = int(item.strip())
            if value < 1 or value > total:
                raise ValueError(f"题号必须在 1 到 {total} 之间：{value}")
            if value not in selected:
                selected.append(value)
    return selected


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """写入 UTF-8 JSON 结果，便于后续比较不同模型或提示词版本。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> int:
    schema, published_schema_metadata = load_semantic_schema(
        args.tenant_id,
        args.dataset_id,
    )
    case_numbers = _parse_case_selection(args.case, len(CASES))
    runtime = None
    if not args.dry_run:
        runtime = await build_llm_runtime(
            args.model_id,
            no_reasoning=args.no_reasoning,
        )

    results: list[dict[str, Any]] = []
    context_documents: list[str] = []
    client = (
        LangChainGenerationModelClient(runtime.llm) if runtime is not None else None
    )
    for number in case_numbers:
        case = CASES[number - 1]
        semantic_yaml, context_metadata = build_semantic_yaml(case, schema)
        context_documents.append(
            f"# case={number} question={case.question}\n{semantic_yaml.rstrip()}"
        )
        messages = build_messages(case.question, semantic_yaml)
        item: dict[str, Any] = {
            "number": number,
            "question": case.question,
            "expected_sql": case.expected_sql.strip(),
            "semantic_context": context_metadata,
            "prompt_chars": sum(len(message.content) for message in messages),
        }
        if args.dry_run:
            item["status"] = "dry_run"
            results.append(item)
            continue

        assert client is not None
        chunks = list(client.stream(messages))
        raw_output = "".join(chunk.content for chunk in chunks)
        usage = dict(chunks[-1].token_usage) if chunks else {}
        item["raw_output"] = raw_output
        item["usage"] = usage
        try:
            generated = parse_sql_generation_result(raw_output)
        except SQLGenerationError as exc:
            item["status"] = "parse_failed"
            item["error"] = str(exc)
            results.append(item)
            print(f"[FAIL] case={number:02d} SQL 解析失败：{exc}")
            continue

        evaluation = evaluate_sql(case, generated.sql, generated.tables)
        item.update(
            {
                "status": "passed" if evaluation["passed"] else "failed",
                "generated_sql": generated.sql,
                "generated_tables": generated.tables,
                "chart_type": generated.chart_type,
                "brief": generated.brief,
                "evaluation": evaluation,
            }
        )
        results.append(item)
        print(
            f"[{item['status'].upper()}] case={number:02d} "
            f"table={','.join(evaluation['actual_tables']) or '-'}"
        )

    output = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "mode": "direct_model_sql_generation",
        "dataset_id": args.dataset_id,
        "tenant_id": args.tenant_id,
        "dry_run": args.dry_run,
        "model": (
            {
                "model_id": runtime.config.model_id,
                "model_type": runtime.config.model_type,
                "model_name": runtime.config.model_name,
                "api_base_host": (
                    runtime.config.api_base_url.split("//", 1)[-1].split("/", 1)[0]
                    if runtime is not None and runtime.config.api_base_url
                    else None
                ),
            }
            if runtime is not None
            else None
        ),
        "published_schema": published_schema_metadata,
        "cases": results,
        "summary": {
            "total": len(results),
            "passed": sum(item.get("status") == "passed" for item in results),
            "failed": sum(item.get("status") == "failed" for item in results),
            "parse_failed": sum(
                item.get("status") == "parse_failed" for item in results
            ),
        },
    }
    _write_json(args.output, output)
    if args.context_output:
        args.context_output.parent.mkdir(parents=True, exist_ok=True)
        args.context_output.write_text(
            "\n---\n".join(context_documents) + "\n",
            encoding="utf-8",
        )

    print(
        f"完成：{len(results)} 题，"
        f"通过 {output['summary']['passed']} 题，"
        f"失败 {output['summary']['failed'] + output['summary']['parse_failed']} 题。"
    )
    print(f"结果：{args.output}")
    if args.context_output:
        print(f"语义资产长上下文：{args.context_output}")
    return (
        0
        if output["summary"]["failed"] == 0 and output["summary"]["parse_failed"] == 0
        else 1
    )


def parse_args() -> argparse.Namespace:
    """定义命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", type=int, default=DEFAULT_DATASET_ID)
    parser.add_argument("--tenant-id", type=int, default=DEFAULT_TENANT_ID)
    parser.add_argument(
        "--model-id",
        type=int,
        default=None,
        help="指定模型配置 ID；不指定时使用系统默认模型。",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="测试题号，可重复或使用逗号分隔，例如 --case 1 --case 3,4。",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--context-output",
        type=Path,
        default=DEFAULT_CONTEXT_OUTPUT,
        help="保存每道题实际发送给模型的 YAML 文档。",
    )
    parser.add_argument(
        "--no-context-output",
        action="store_true",
        help="不保存 YAML 语义上下文文件。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读取语义资产并构造提示词，不调用模型。",
    )
    parser.add_argument(
        "--no-reasoning",
        action="store_true",
        help="从模型附加参数中关闭思考模式。",
    )
    args = parser.parse_args()
    if args.no_context_output:
        args.context_output = None
    return args


def main() -> int:
    """脚本入口。"""

    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
