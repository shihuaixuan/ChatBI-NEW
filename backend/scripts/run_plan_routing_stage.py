"""手动验证 SemanticParse -> ModeRouter -> ExecutionRequirement 的 Plan 路由阶段。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session

from apps.chatbi.models.dto.execution_requirement import ExecutionRequirement
from apps.chatbi.models.dto.semantic_parse import SemanticParseOutput
from apps.chatbi.orchestration.pipeline.mode_router import (
    ModeRouteInput,
    ModeRouter,
    ModeRoutingError,
)
from apps.semantic.composition import build_semantic_schema_service
from apps.temporal import build_temporal_context
from common.core.db import engine


class PlanRoutingStageError(RuntimeError):
    """手动测试输入不完整，或最终没有进入 Plan。"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "从已经完成的语义解析结果开始，验证是否路由到 Plan，"
            "并打印经过 DTO 校验的 ExecutionRequirement。"
        )
    )
    parser.add_argument("--tenant-id", type=int, required=True, help="租户 ID")
    parser.add_argument("--dataset-id", type=int, required=True, help="语义数据集 ID")
    parser.add_argument("--datasource-id", type=int, help="可选的数据源 ID")
    parser.add_argument(
        "--semantic-parse-file",
        type=Path,
        help="完整 SemanticParseOutput JSON 文件；与 --metric-ref 二选一",
    )
    parser.add_argument(
        "--candidate-groups-file",
        type=Path,
        help="可选的候选分组 JSON；未传时根据 SemanticParse 中的 ref 自动构造",
    )
    parser.add_argument(
        "--metric-ref",
        action="append",
        default=[],
        help="简单场景的指标引用，可重复，例如 METRIC:271:246",
    )
    parser.add_argument(
        "--dimension-ref",
        action="append",
        default=[],
        help="简单场景的分组维度引用，可重复",
    )
    parser.add_argument(
        "--reference-at",
        help="时间解析基准，ISO 8601 格式且必须包含时区；默认使用当前时间",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Shanghai",
        help="时间解析时区，默认 Asia/Shanghai",
    )
    return parser.parse_args()


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """读取 JSON 对象，输入错误直接终止测试。"""

    if not path.is_file():
        raise PlanRoutingStageError(f"{label} 文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PlanRoutingStageError(f"{label} 必须是 JSON 对象")
    return payload


def _semantic_parse_from_args(args: argparse.Namespace) -> SemanticParseOutput:
    """读取完整语义解析结果，或为跨模型简单场景构造最小合法输入。"""

    if args.semantic_parse_file is not None:
        if args.metric_ref or args.dimension_ref:
            raise PlanRoutingStageError(
                "--semantic-parse-file 不能与 --metric-ref/--dimension-ref 同时使用"
            )
        payload = _load_json_object(
            args.semantic_parse_file,
            label="SemanticParse",
        )
    else:
        if not args.metric_ref:
            raise PlanRoutingStageError(
                "必须提供 --semantic-parse-file，或至少提供一个 --metric-ref"
            )
        payload = {
            "status": "resolved",
            "measures": [{"ref": ref} for ref in args.metric_ref],
            "group_by": [{"ref": ref} for ref in args.dimension_ref],
            "filters": [],
            "time_filters": [],
            "order_by": [],
            "calculations": [],
            "unresolved": [],
        }
    return SemanticParseOutput.model_validate(payload)


def _selected_refs(semantic_parse: SemanticParseOutput) -> tuple[str, ...]:
    """候选分组必须覆盖语义解析选中的全部资产引用。"""

    return tuple(
        dict.fromkeys(
            [
                *(item.ref for item in semantic_parse.measures),
                *(item.ref for item in semantic_parse.group_by),
                *(item.target_ref for item in semantic_parse.filters),
                *(item.target_ref for item in semantic_parse.order_by),
            ]
        )
    )


def _candidate_groups_from_args(
    args: argparse.Namespace,
    semantic_parse: SemanticParseOutput,
) -> dict[str, list[dict[str, Any]]]:
    """使用检索阶段的真实输出，或仅根据已绑定 ref 构造路由所需候选分组。"""

    if args.candidate_groups_file is not None:
        payload = _load_json_object(
            args.candidate_groups_file,
            label="CandidateGroups",
        )
        candidate_groups = payload.get("candidate_groups", payload)
        if not isinstance(candidate_groups, dict):
            raise PlanRoutingStageError("CandidateGroups 缺少候选分组对象")
        return {
            str(name): [item for item in items if isinstance(item, dict)]
            for name, items in candidate_groups.items()
            if isinstance(items, list)
        }

    groups: dict[str, list[dict[str, Any]]] = {
        "metrics": [],
        "dimensions": [],
    }
    for ref in _selected_refs(semantic_parse):
        if ref.startswith("METRIC:"):
            groups["metrics"].append({"ref": ref})
        elif ref.startswith("DIMENSION:"):
            groups["dimensions"].append({"ref": ref})
        else:
            raise PlanRoutingStageError(f"不支持的语义资产引用：{ref}")
    return groups


def _reference_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PlanRoutingStageError("--reference-at 不是合法的 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PlanRoutingStageError(
            "--reference-at 必须包含时区，例如 2026-08-19T12:00:00+08:00"
        )
    return parsed


def main() -> int:
    args = _parse_args()
    if args.tenant_id <= 0 or args.dataset_id <= 0:
        raise PlanRoutingStageError("tenant-id 和 dataset-id 必须为正整数")
    if args.datasource_id is not None and args.datasource_id <= 0:
        raise PlanRoutingStageError("datasource-id 必须为正整数")

    semantic_parse = _semantic_parse_from_args(args)
    candidate_groups = _candidate_groups_from_args(args, semantic_parse)
    temporal_context = build_temporal_context(
        reference_at=_reference_at(args.reference_at),
        timezone=args.timezone,
    )

    print("\n=== 阶段输入：SemanticParse ===")
    print(
        json.dumps(
            semantic_parse.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n=== 阶段输入：CandidateGroups ===")
    print(json.dumps(candidate_groups, ensure_ascii=False, indent=2))

    # 本脚本只读真实语义 Schema，不创建 Run，也不执行 SQL。
    with Session(engine) as session:
        router = ModeRouter(build_semantic_schema_service(session))
        route_payload = router.route(
            ModeRouteInput(
                semantic_parse=semantic_parse,
                candidate_groups=candidate_groups,
                dataset_id=args.dataset_id,
                tenant_id=args.tenant_id,
                enabled_modes=("fast", "plan"),
                temporal_context=temporal_context,
                datasource_id=args.datasource_id,
            )
        )

    requirement = ExecutionRequirement.model_validate(route_payload)
    if requirement.route.mode != "plan":
        raise PlanRoutingStageError(
            f"预期路由到 plan，实际路由到 {requirement.route.mode}："
            f"{','.join(requirement.route.reasons)}"
        )
    if requirement.unresolved:
        raise PlanRoutingStageError("ExecutionRequirement 仍包含未解决项")
    requirement.require_ready("plan")

    print("\n=== 路由结果 ===")
    print(
        json.dumps(
            requirement.route.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n=== 最终 ExecutionRequirement ===")
    print(
        json.dumps(
            requirement.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n测试通过：SemanticParse 已路由到 Plan，ExecutionRequirement 校验成功。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        PlanRoutingStageError,
        ValidationError,
        ModeRoutingError,
        json.JSONDecodeError,
    ) as exc:
        raise SystemExit(f"测试失败：{exc}") from exc
