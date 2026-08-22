"""只读盘点 dataset 243 可用于 Research 第三阶段验收的真实资产。

输出：
1. 数据集包含的模型；
2. 每个模型的指标（含可加性相关字段）；
3. 每个模型的物理维度；
4. 领域内逻辑维度与物理绑定；
5. 现有治理资产（层级、指标关系、维度能力）现状。
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from apps.semantic.models.orm import (
    DimensionHierarchy,
    DimensionHierarchyLevel,
    LogicalDimension,
    MetricDimensionCapability,
    MetricRelationship,
    SemanticDataset,
    SemanticDatasetModelConfig,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticModelRelation,
)
from common.core.db import engine

DATASET_ID = 243


def main() -> None:
    with Session(engine) as session:
        dataset = session.get(SemanticDataset, DATASET_ID)
        if dataset is None:
            raise SystemExit(f"dataset {DATASET_ID} not found")
        print(f"=== dataset {dataset.id}: {dataset.name} (oid={dataset.oid}) ===")

        model_configs = session.exec(
            select(SemanticDatasetModelConfig).where(
                SemanticDatasetModelConfig.oid == dataset.oid,
                SemanticDatasetModelConfig.dataset_id == DATASET_ID,
                SemanticDatasetModelConfig.status == 1,
            )
        ).all()
        model_ids = sorted({c.model_id for c in model_configs})
        print(
            "model configs: "
            + json.dumps(
                [
                    {
                        "model_id": c.model_id,
                        "includes_all": c.includes_all,
                        "is_default": c.is_default,
                    }
                    for c in model_configs
                ],
                ensure_ascii=False,
            )
        )
        print(f"model ids: {model_ids}")

        models = {
            m.id: m
            for m in session.exec(
                select(SemanticModel).where(SemanticModel.id.in_(model_ids))
            ).all()
        }

        metrics = session.exec(
            select(SemanticMetric).where(
                SemanticMetric.model_id.in_(model_ids), SemanticMetric.status == 1
            )
        ).all()
        print(f"\n=== metrics ({len(metrics)}) ===")
        for m in sorted(metrics, key=lambda x: (x.model_id, x.id or 0)):
            ext_keys = sorted((m.ext or {}).keys())
            tp_keys = sorted((m.type_params or {}).keys())
            print(
                f"[{m.id}] model={m.model_id} name={m.name!r} biz={m.biz_name!r} "
                f"type={m.type} agg={m.default_agg} ext_keys={ext_keys} tp_keys={tp_keys}"
            )

        dims = session.exec(
            select(SemanticDimension).where(
                SemanticDimension.model_id.in_(model_ids), SemanticDimension.status == 1
            )
        ).all()
        print(f"\n=== physical dimensions ({len(dims)}) ===")
        for d in sorted(dims, key=lambda x: (x.model_id, x.id or 0)):
            print(
                f"[{d.id}] model={d.model_id} name={d.name!r} biz={d.biz_name!r} "
                f"type={d.type} semantic={d.semantic_type} time={d.is_default_time} "
                f"logi={d.logical_dimension_id}"
            )

        relations = session.exec(
            select(SemanticModelRelation).where(
                SemanticModelRelation.oid == dataset.oid,
                SemanticModelRelation.status == 1,
            )
        ).all()
        print(f"\n=== model relations ({len(relations)}) ===")
        for r in relations:
            print(
                f"[{r.id}] oid={r.oid} {r.__class__.__name__} "
                f"fields={ {k: getattr(r, k) for k in ('source_model_id', 'target_model_id', 'relation_type', 'status') if hasattr(r, k)} }"
            )

        logical_dims = session.exec(
            select(LogicalDimension).where(
                LogicalDimension.oid == dataset.oid, LogicalDimension.status == 1
            )
        ).all()
        print(f"\n=== logical dimensions ({len(logical_dims)}) ===")
        for ld in logical_dims:
            print(
                f"[{ld.id}] name={ld.name!r} biz={ld.biz_name!r} "
                f"semantic={ld.semantic_type} entity={ld.entity_id}"
            )

        hierarchies = session.exec(
            select(DimensionHierarchy).where(DimensionHierarchy.oid == dataset.oid)
        ).all()
        print(f"\n=== dimension hierarchies ({len(hierarchies)}) ===")
        for h in hierarchies:
            levels = session.exec(
                select(DimensionHierarchyLevel).where(
                    DimensionHierarchyLevel.hierarchy_id == h.id
                )
            ).all()
            print(
                f"[{h.id}] {h.name!r} type={h.hierarchy_type} "
                f"contract={h.contract_status} v{h.version} levels={len(levels)}"
            )

        rels = session.exec(
            select(MetricRelationship).where(MetricRelationship.oid == dataset.oid)
        ).all()
        print(f"\n=== metric relationships ({len(rels)}) ===")
        for r in rels:
            print(
                f"[{r.id}] target={r.target_metric_id} driver={r.driver_metric_id} "
                f"type={r.relationship_type} validation={r.validation_method} "
                f"direction={r.expected_direction} roles={r.supported_time_roles} "
                f"contract={r.contract_status} v{r.version}"
            )

        caps = session.exec(
            select(MetricDimensionCapability).where(
                MetricDimensionCapability.oid == dataset.oid,
                MetricDimensionCapability.status == 1,
            )
        ).all()
        usage_counter: dict[str, int] = {}
        for c in caps:
            for u in c.usages or []:
                usage_counter[u] = usage_counter.get(u, 0) + 1
        print(f"\n=== metric dimension capabilities ({len(caps)}) ===")
        print(f"usage distribution: {json.dumps(usage_counter, ensure_ascii=False)}")
        contrib = [c for c in caps if "CONTRIBUTION" in (c.usages or [])]
        print(f"CONTRIBUTION capabilities: {len(contrib)}")


if __name__ == "__main__":
    main()
