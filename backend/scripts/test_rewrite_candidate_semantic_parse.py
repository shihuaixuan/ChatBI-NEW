#!/usr/bin/env python3
"""测试“问题重写 -> 候选检索 -> 语义解析模型”完整流程。

流程：

    模拟问题重写
        -> RetrievalRequest
        -> 候选资产检索
        -> 候选结果
        -> 语义解析模型
        -> SemanticParseOutput

问题重写仍使用脚本内的模拟数据。运行时会先从指定数据集 Schema 校验指标和维度
短语，确保模拟数据确实存在于当前资产库。语义解析阶段使用系统配置的结构化模型，
不执行 SQL，也不进入 Fast/Plan。

默认运行：

    python backend/scripts/test_rewrite_candidate_semantic_parse.py

只运行一个案例：

    python backend/scripts/test_rewrite_candidate_semantic_parse.py \
        --case 单指标按店铺统计
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 允许从仓库根目录直接运行脚本时导入 backend 下的项目模块。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session  # noqa: E402

from apps.chatbi.composition import build_semantic_parse_service  # noqa: E402
from apps.chatbi.errors import (  # noqa: E402
    QuestionModelError,
    QuestionUnderstandingError,
)
from apps.retrieval.errors import RetrievalQueryError  # noqa: E402
from apps.retrieval.query.service import (  # noqa: E402
    build_retrieval_request,
    build_retrieval_service,
)
from apps.semantic.composition import build_semantic_schema_service  # noqa: E402
from apps.semantic.models.dto.dataset_schema import DatasetSchema  # noqa: E402
from common.core.db import engine  # noqa: E402

DEFAULT_TENANT_ID = 1
DEFAULT_ACTOR_ID = 1
DEFAULT_DATASET_ID = 243


# 这些数据模拟问题重写模型的结构化输出，短语来自 P1 测试数据集资产。
SIMULATED_REWRITES: tuple[dict[str, Any], ...] = (
    {
        "name": "单指标按店铺统计",
        "category": "single_metric",
        "original_question": "2026年6月各店铺的总GMV是多少？",
        "rewrite_question": "统计2026年6月各店铺的总GMV。",
        "metric_phrases": ["总GMV"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "多指标按店铺统计",
        "category": "multi_metric_same_model",
        "original_question": "2026年6月各店铺的总GMV和总订单数是多少？",
        "rewrite_question": "统计2026年6月各店铺的总GMV和总订单数。",
        "metric_phrases": ["总GMV", "总订单数"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "单指标带店铺条件",
        "category": "single_metric_with_filter",
        "original_question": "2026年6月店铺100023的订单数是多少？",
        "rewrite_question": "统计2026年6月店铺100023的总订单数。",
        "metric_phrases": ["总订单数"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "单指标无分组",
        "category": "single_metric",
        "original_question": "2026年6月的总GMV是多少？",
        "rewrite_question": "统计2026年6月的总GMV。",
        "metric_phrases": ["总GMV"],
        "dimension_phrases": [],
    },
    {
        "name": "指标别名按门店统计",
        "category": "single_metric_alias",
        "original_question": "2026年6月各门店的销售额是多少？",
        "rewrite_question": "统计2026年6月各门店的销售额。",
        "metric_phrases": ["销售额"],
        "dimension_phrases": ["门店"],
    },
    {
        "name": "按渠道统计",
        "category": "single_metric_grouped",
        "original_question": "2026年6月线上和线下的总订单数分别是多少？",
        "rewrite_question": "按交易渠道统计2026年6月的总订单数。",
        "metric_phrases": ["总订单数"],
        "dimension_phrases": ["渠道"],
    },
    {
        "name": "多维度统计",
        "category": "single_metric_multi_group",
        "original_question": "2026年6月各店铺各渠道的销售类GMV是多少？",
        "rewrite_question": "按店铺和交易渠道统计2026年6月的销售类GMV。",
        "metric_phrases": ["销售类GMV"],
        "dimension_phrases": ["店铺", "渠道"],
    },
    {
        "name": "库存指标按门店统计",
        "category": "single_metric_alias",
        "original_question": "现在各门店的库存量是多少？",
        "rewrite_question": "按门店统计当前库存量。",
        "metric_phrases": ["库存量"],
        "dimension_phrases": ["门店"],
    },
    {
        "name": "客户名称条件",
        "category": "single_metric_with_filter",
        "original_question": "客户名称为华东商贸的客户当日GMV是多少？",
        "rewrite_question": "查询客户名称为华东商贸的客户当日GMV。",
        "metric_phrases": ["客户当日GMV"],
        "dimension_phrases": ["客户名称"],
    },
    {
        "name": "多指标不同模型",
        "category": "multi_metric_cross_model",
        "original_question": "2026年6月各店铺的总GMV和订单金额是多少？",
        "rewrite_question": "按店铺统计2026年6月的总GMV和订单金额。",
        "metric_phrases": ["总GMV", "订单金额"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "计算增长率",
        "category": "calculation_growth",
        "original_question": "2026年6月的总GMV比2025年6月增长了多少？",
        "rewrite_question": "比较2026年6月和2025年6月的总GMV，计算增长额和增长率。",
        "metric_phrases": ["总GMV"],
        "dimension_phrases": [],
    },
    {
        "name": "计算指标占比",
        "category": "calculation_share",
        "original_question": "2026年6月各店铺销售类GMV占总GMV的比例是多少？",
        "rewrite_question": "按店铺统计2026年6月的销售类GMV和总GMV，并计算销售类GMV占总GMV的比例。",
        "metric_phrases": ["销售类GMV", "总GMV"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "店铺GMV排名",
        "category": "ranking_topn",
        "original_question": "2026年6月GMV最高的10个店铺是哪些？",
        "rewrite_question": "按总GMV降序统计2026年6月排名前10的店铺。",
        "metric_phrases": ["总GMV"],
        "dimension_phrases": ["店铺"],
    },
)


def _asset_texts(elements: list[Any]) -> set[str]:
    """提取资产名称和别名，用于验证模拟短语来自当前资产库。"""

    texts: set[str] = set()
    for element in elements:
        for value in (
            getattr(element, "name", None),
            getattr(element, "biz_name", None),
            *(getattr(element, "alias", None) or []),
        ):
            text = str(value or "").strip()
            if text:
                texts.add(text.casefold())
    return texts


def _validate_rewrite_phrases(schema: DatasetSchema, rewrite: dict[str, Any]) -> None:
    """确认模拟重写输出的短语确实存在于当前数据集资产库。"""

    metric_texts = _asset_texts(schema.metrics)
    dimension_texts = _asset_texts(schema.dimensions)
    missing_metrics = [
        phrase
        for phrase in rewrite["metric_phrases"]
        if str(phrase).strip().casefold() not in metric_texts
    ]
    missing_dimensions = [
        phrase
        for phrase in rewrite["dimension_phrases"]
        if str(phrase).strip().casefold() not in dimension_texts
    ]
    if missing_metrics or missing_dimensions:
        raise RuntimeError(
            "模拟问题重写短语不在当前资产库中："
            f"missing_metrics={missing_metrics}, "
            f"missing_dimensions={missing_dimensions}"
        )


def _print_json(title: str, value: Any) -> None:
    """以便于人工检查的格式打印 JSON。"""

    print(f"\n--- {title} ---")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _candidate_result(payload: dict[str, Any]) -> dict[str, Any]:
    """保留候选阶段返回给语义解析模型的完整候选分组。"""

    return {
        "hit": payload.get("hit", False),
        "status": payload.get("status"),
        "candidate_groups": payload.get("candidate_groups", {}),
        "retrieval_diagnostics": payload.get("retrieval_diagnostics", {}),
    }


def run_case(
    schema: DatasetSchema,
    retrieval_service: Any,
    semantic_parse_service: Any,
    rewrite: dict[str, Any],
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
) -> bool:
    """执行一个模拟重写结果到语义解析 JSON 的完整案例。"""

    _validate_rewrite_phrases(schema, rewrite)
    request = build_retrieval_request(
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        metric_phrases=list(rewrite["metric_phrases"]),
        dimension_phrases=list(rewrite["dimension_phrases"]),
        request_id=f"rewrite-semantic-parse-{dataset_id}-{rewrite['name']}",
    )
    retrieval = retrieval_service.retrieve(request)
    candidate_result = _candidate_result(retrieval.payload)
    if not candidate_result["hit"]:
        raise RuntimeError("候选资产未命中，不能进入语义解析模型")

    semantic_output = semantic_parse_service.parse(
        rewrite_question=rewrite["rewrite_question"],
        candidate_payload=candidate_result,
    )

    _print_json(
        rewrite["name"],
        {
            "original_question": rewrite["original_question"],
            "category": rewrite.get("category", "basic_query"),
            "rewrite_output": {
                "rewrite_question": rewrite["rewrite_question"],
                "metric_phrases": rewrite["metric_phrases"],
                "dimension_phrases": rewrite["dimension_phrases"],
            },
            "retrieval_request": request.model_dump(mode="json"),
            "candidate_result": candidate_result,
            "semantic_parse_json": semantic_output.model_dump(mode="json"),
        },
    )
    return True


def parse_args() -> argparse.Namespace:
    """解析脚本参数。"""

    parser = argparse.ArgumentParser(
        description="测试问题重写到语义解析 JSON 的完整流程"
    )
    parser.add_argument("--tenant-id", type=int, default=DEFAULT_TENANT_ID)
    parser.add_argument("--actor-id", type=int, default=DEFAULT_ACTOR_ID)
    parser.add_argument("--dataset-id", type=int, default=DEFAULT_DATASET_ID)
    parser.add_argument(
        "--case",
        dest="case_name",
        choices=[item["name"] for item in SIMULATED_REWRITES],
        help="只运行一个模拟问题",
    )
    return parser.parse_args()


def main() -> int:
    """脚本入口。"""

    args = parse_args()
    cases = tuple(
        item
        for item in SIMULATED_REWRITES
        if args.case_name is None or item["name"] == args.case_name
    )
    passed = 0

    with Session(engine) as session:
        schema_provider = build_semantic_schema_service(session)
        schema = schema_provider.build_dataset_schema(
            args.tenant_id,
            args.dataset_id,
        )
        retrieval_service = build_retrieval_service(
            session,
            schema_provider=schema_provider,
        )
        semantic_parse_service = build_semantic_parse_service()
        for rewrite in cases:
            try:
                if run_case(
                    schema,
                    retrieval_service,
                    semantic_parse_service,
                    rewrite,
                    tenant_id=args.tenant_id,
                    actor_id=args.actor_id,
                    dataset_id=args.dataset_id,
                ):
                    passed += 1
            except (
                QuestionModelError,
                QuestionUnderstandingError,
                RetrievalQueryError,
                RuntimeError,
                TimeoutError,
                ValueError,
            ) as exc:
                # 测试脚本保留具体阶段错误，便于判断失败发生在检索还是语义解析。
                print(f"\n--- {rewrite['name']}：FAIL ---")
                print(f"{type(exc).__name__}: {exc}")
                details = getattr(exc, "details", None)
                if details:
                    print(json.dumps(details, ensure_ascii=False, indent=2, default=str))

    print(f"\n汇总：{passed}/{len(cases)} 个案例完成语义解析")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
