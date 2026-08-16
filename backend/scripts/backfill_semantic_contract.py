"""预检并保守回填存量语义契约；默认只输出报告。"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDatasetInstruction,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
from apps.semantic.repository.sqlmodel.results import all_results
from apps.semantic.services.contract_backfill import (
    SemanticContractBackfillPlan,
    plan_semantic_contract_backfill,
)
from common.core.db import engine

STRICT_COVERAGE_FIELDS = (
    "model_grain_rate",
    "metric_contract_rate",
    "dimension_binding_rate",
    "metric_capability_rate",
    "relation_contract_rate",
    "default_time_dimension_rate",
)
STRICT_REQUIRED_INSTRUCTION_MODULES = ("sql_generation", "question_categorization")


def evaluate_strict_readiness(
    *,
    dataset_id: int,
    semantic_enforcement: str,
    coverage: dict[str, float],
    configured_modules: set[str],
) -> dict[str, Any]:
    """按统一门槛计算单个数据集的 STRICT 推广准备度。"""

    coverage_payload = {
        field: float(coverage.get(field, 0.0)) for field in STRICT_COVERAGE_FIELDS
    }
    coverage_ready = all(value >= 1.0 for value in coverage_payload.values())
    missing_modules = [
        module
        for module in STRICT_REQUIRED_INSTRUCTION_MODULES
        if module not in configured_modules
    ]
    coverage_reasons: list[str] = []
    if not coverage_ready:
        coverage_reasons.extend(
            f"{field}<{1.0:g}"
            for field, value in coverage_payload.items()
            if value < 1.0
        )
    reasons = list(coverage_reasons)
    if missing_modules:
        reasons.append("missing_instruction_modules:" + ",".join(missing_modules))
    return {
        "dataset_id": dataset_id,
        "semantic_enforcement": semantic_enforcement,
        "coverage": coverage_payload,
        "coverage_ready": coverage_ready,
        "strict_blockers": coverage_reasons,
        "instructions": {"configured_modules": sorted(configured_modules)},
        "instructions_ready": not missing_modules,
        # STRICT 的硬门槛是契约覆盖率；instructions 缺失单独报告，不改变语义执行模式。
        "ready_for_strict": coverage_ready,
        "reasons": reasons,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成存量语义契约覆盖率和审核清单；默认不写数据库",
    )
    parser.add_argument("--oid", type=int, help="只处理指定租户")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="只应用可由旧字段确定的 DRAFT 状态、绑定角色和优先级",
    )
    return parser.parse_args()


def build_plan(session: Session, oid: int | None) -> SemanticContractBackfillPlan:
    """从数据库快照生成幂等回填计划。"""

    def load(model: Any) -> list[Any]:
        statement = select(model)
        if oid is not None:
            statement = statement.where(model.oid == oid)
        return all_results(session.exec(statement))

    return plan_semantic_contract_backfill(
        models=load(SemanticModel),
        metrics=load(SemanticMetric),
        dimensions=load(SemanticDimension),
        relations=load(SemanticModelRelation),
        datasets=load(SemanticDataset),
        dataset_model_configs=load(SemanticDatasetModelConfig),
        capabilities=load(MetricDimensionCapability),
    )


def apply_plan(session: Session, plan: SemanticContractBackfillPlan) -> int:
    """只写入计划中的确定性字段；人工审核项不会自动落库。"""

    model_by_type: dict[str, Any] = {
        "MODEL": SemanticModel,
        "DIMENSION": SemanticDimension,
        "RELATION": SemanticModelRelation,
    }
    applied = 0
    for update in plan.updates:
        model = model_by_type[update.asset_type]
        asset = session.exec(
            select(model).where(col(model.id) == update.asset_id)
        ).first()
        if asset is None:
            raise ValueError(
                f"回填资产不存在: {update.asset_type}:{update.asset_id}"
            )
        changed = False
        for field_name, value in update.values.items():
            if getattr(asset, field_name) is None:
                setattr(asset, field_name, value)
                changed = True
        if changed:
            session.add(asset)
            applied += 1
    return applied


def build_strict_readiness(
    session: Session,
    plan: SemanticContractBackfillPlan,
    oid: int | None,
) -> list[dict[str, Any]]:
    """输出逐数据集 STRICT 推广报告；只报告，不修改 semanticEnforcement。"""

    dataset_statement = select(SemanticDataset).where(SemanticDataset.status == 1)
    instruction_statement = select(SemanticDatasetInstruction).where(
        SemanticDatasetInstruction.enabled.is_(True)
    )
    if oid is not None:
        dataset_statement = dataset_statement.where(SemanticDataset.oid == oid)
        instruction_statement = instruction_statement.where(SemanticDatasetInstruction.oid == oid)
    datasets = all_results(session.exec(dataset_statement))
    instructions = all_results(session.exec(instruction_statement))
    instruction_modules: dict[int, set[str]] = {}
    for instruction in instructions:
        instruction_modules.setdefault(instruction.dataset_id, set()).add(instruction.module)
    coverage_by_dataset = {item.dataset_id: item for item in plan.coverage}
    report: list[dict[str, Any]] = []
    for dataset in datasets:
        if dataset.id is None:
            continue
        coverage = coverage_by_dataset.get(dataset.id)
        coverage_payload = {
            field: getattr(coverage, field, 0.0) if coverage is not None else 0.0
            for field in STRICT_COVERAGE_FIELDS
        }
        configured_modules = sorted(instruction_modules.get(dataset.id, set()))
        report.append(
            evaluate_strict_readiness(
                dataset_id=dataset.id,
                semantic_enforcement=str(
                    (dataset.query_config or {}).get("semanticEnforcement") or "LEGACY"
                ).upper(),
                coverage=coverage_payload,
                configured_modules=set(configured_modules),
            )
        )
    return report


def main() -> None:
    args = parse_args()
    if args.oid is not None and args.oid <= 0:
        raise ValueError("--oid 必须是正整数")
    with Session(engine) as session:
        plan = build_plan(session, args.oid)
        summary = plan.to_summary()
        summary["strict_readiness"] = build_strict_readiness(
            session,
            plan,
            args.oid,
        )
        if args.apply:
            summary["applied_count"] = apply_plan(session, plan)
            session.commit()
        else:
            summary["applied_count"] = 0
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
