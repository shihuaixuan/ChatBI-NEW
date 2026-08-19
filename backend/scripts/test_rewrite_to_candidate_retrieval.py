#!/usr/bin/env python3
"""测试“问题重写 -> RetrievalRequest -> 候选资产检索 -> 候选结果”流程。

本脚本不调用问题重写模型，而是模拟问题重写模型的结构化输出。
脚本会先从指定数据集的 Schema 中校验模拟短语，确认它们确实存在于当前资产库，
然后将短语交给统一候选检索服务。当前流程在候选结果返回后结束，不执行资产绑定。

默认使用 P1 测试数据集：

    python backend/scripts/test_rewrite_to_candidate_retrieval.py

如果数据库连接或测试数据集不是默认配置，可以覆盖租户、用户和数据集：

    python backend/scripts/test_rewrite_to_candidate_retrieval.py \
        --tenant-id 1 \
        --actor-id 1 \
        --dataset-id 243
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


# 这些数据模拟问题重写模型的输出，短语来自 P1 测试数据集的语义资产。
SIMULATED_REWRITES: tuple[dict[str, Any], ...] = (
    {
        "name": "单指标按店铺统计",
        "original_question": "2026年6月各店铺的总GMV是多少？",
        "rewrite_question": "统计2026年6月各店铺的总GMV。",
        "metric_phrases": ["总GMV"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "多指标按店铺统计",
        "original_question": "2026年6月各店铺的总GMV和总订单数是多少？",
        "rewrite_question": "统计2026年6月各店铺的总GMV和总订单数。",
        "metric_phrases": ["总GMV", "总订单数"],
        "dimension_phrases": ["店铺"],
    },
    {
        "name": "单指标带店铺条件",
        "original_question": "2026年6月店铺100023的订单数是多少？",
        "rewrite_question": "统计2026年6月店铺100023的总订单数。",
        "metric_phrases": ["总订单数"],
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


def _validate_rewrite_phrases(
    schema: DatasetSchema,
    rewrite: dict[str, Any],
) -> None:
    """确认问题重写输出的指标和维度短语在资产库中真实存在。"""

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


def _candidate_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留本次流程需要观察的候选结果字段。"""

    return {
        "hit": payload.get("hit", False),
        "status": payload.get("status"),
        "candidate_groups": payload.get("candidate_groups", {}),
        "retrieval_diagnostics": payload.get("retrieval_diagnostics", {}),
    }


def _print_json(title: str, value: Any) -> None:
    """以便于人工检查的格式打印脚本结果。"""

    print(f"\n--- {title} ---")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def run_case(
    session: Session,
    schema: DatasetSchema,
    rewrite: dict[str, Any],
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
) -> bool:
    """执行一个模拟重写结果到候选结果的完整流程。"""

    _validate_rewrite_phrases(schema, rewrite)
    request = build_retrieval_request(
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        metric_phrases=list(rewrite["metric_phrases"]),
        dimension_phrases=list(rewrite["dimension_phrases"]),
        request_id=f"rewrite-candidate-{dataset_id}-{rewrite['name']}",
    )
    result = build_retrieval_service(session).retrieve(request)
    candidate_result = _candidate_result_payload(result.payload)

    _print_json(
        rewrite["name"],
        {
            "original_question": rewrite["original_question"],
            "rewrite_output": {
                "rewrite_question": rewrite["rewrite_question"],
                "metric_phrases": rewrite["metric_phrases"],
                "dimension_phrases": rewrite["dimension_phrases"],
            },
            "retrieval_request": request.model_dump(mode="json"),
            "candidate_result": candidate_result,
        },
    )
    return bool(candidate_result["hit"])


def parse_args() -> argparse.Namespace:
    """解析脚本参数。"""

    parser = argparse.ArgumentParser(
        description="测试问题重写到候选资产结果的完整流程"
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
        schema = build_semantic_schema_service(session).build_dataset_schema(
            args.tenant_id,
            args.dataset_id,
        )
        for rewrite in cases:
            try:
                if run_case(
                    session,
                    schema,
                    rewrite,
                    tenant_id=args.tenant_id,
                    actor_id=args.actor_id,
                    dataset_id=args.dataset_id,
                ):
                    passed += 1
            except (RetrievalQueryError, RuntimeError, TimeoutError, ValueError) as exc:
                # 测试脚本需要保留具体失败原因，不能只输出“检索失败”。
                print(f"\n--- {rewrite['name']}：FAIL ---")
                print(f"{type(exc).__name__}: {exc}")

    print(f"\n汇总：{passed}/{len(cases)} 个案例召回到候选资产")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
