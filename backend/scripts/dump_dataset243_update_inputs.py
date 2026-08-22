"""只读导出 dataset 243 更新入参所需的当前状态（配置脚本前置检查）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session, select  # noqa: E402

from apps.semantic.models.orm import (  # noqa: E402
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
)
from common.core.db import engine  # noqa: E402

DATASET_ID = 243


def main() -> None:
    with Session(engine) as session:
        dataset = session.get(SemanticDataset, DATASET_ID)
        if dataset is None:
            raise SystemExit(f"dataset {DATASET_ID} not found")
        print(f"oid={dataset.oid} domain_id={dataset.domain_id}")
        for field in (
            "name",
            "biz_name",
            "description",
            "alias",
            "query_config",
            "owner",
            "default_timezone",
            "calendar_type",
            "week_start_day",
            "fiscal_year_start_month",
            "holiday_calendar_key",
            "schema_version",
            "contract_version",
            "status",
        ):
            print(f"{field} = {getattr(dataset, field)!r}")

        configs = session.exec(
            select(SemanticDatasetModelConfig).where(
                SemanticDatasetModelConfig.oid == dataset.oid,
                SemanticDatasetModelConfig.dataset_id == DATASET_ID,
            )
        ).all()
        print("model_configs =", json.dumps(
            [
                {
                    "model_id": c.model_id,
                    "includes_all": c.includes_all,
                    "is_default": c.is_default,
                    "sort_order": c.sort_order,
                    "status": c.status,
                }
                for c in configs
            ], ensure_ascii=False))

        assets = session.exec(
            select(SemanticDatasetAsset).where(
                SemanticDatasetAsset.oid == dataset.oid,
                SemanticDatasetAsset.dataset_id == DATASET_ID,
            )
        ).all()
        print(f"assets ({len(assets)}) =", json.dumps(
            [
                {
                    "asset_type": a.asset_type,
                    "asset_id": a.asset_id,
                    "model_id": a.model_id,
                    "is_default": a.is_default,
                    "sort_order": a.sort_order,
                    "status": a.status,
                }
                for a in sorted(assets, key=lambda x: (x.asset_type, x.asset_id or 0))
            ], ensure_ascii=False))

        caps = session.exec(
            select(MetricDimensionCapability).where(
                MetricDimensionCapability.oid == dataset.oid,
                MetricDimensionCapability.metric_id == 271,
                MetricDimensionCapability.logical_dimension_id.in_([2, 14]),
            )
        ).all()
        print("capabilities(271 x dims 2/14) =", json.dumps(
            [
                {
                    "id": c.id,
                    "metric_id": c.metric_id,
                    "logical_dimension_id": c.logical_dimension_id,
                    "usages": c.usages,
                    "binding_strategy": c.binding_strategy,
                    "relation_path": c.relation_path,
                    "target_model_id": c.target_model_id,
                    "physical_dimension_id": c.physical_dimension_id,
                    "aggregation_safety": c.aggregation_safety,
                    "pre_aggregation_grain": c.pre_aggregation_grain,
                    "time_alignment_policy": c.time_alignment_policy,
                    "contribution_tolerance": c.contribution_tolerance,
                    "status": c.status,
                    "version": c.version,
                }
                for c in caps
            ], ensure_ascii=False))


if __name__ == "__main__":
    main()
