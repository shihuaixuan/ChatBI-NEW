"""预检并保守回填存量语义契约；默认只输出报告。"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlmodel import Session, col, select

from apps.semantic.models.orm import (
    MetricDimensionCapability,
    SemanticDataset,
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


def main() -> None:
    args = parse_args()
    if args.oid is not None and args.oid <= 0:
        raise ValueError("--oid 必须是正整数")
    with Session(engine) as session:
        plan = build_plan(session, args.oid)
        summary = plan.to_summary()
        if args.apply:
            summary["applied_count"] = apply_plan(session, plan)
            session.commit()
        else:
            summary["applied_count"] = 0
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
