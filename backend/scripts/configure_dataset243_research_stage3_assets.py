"""配置并发布 dataset 243 的 Research 第三阶段验收治理资产。

按已确认的验收方案执行（全部走 Semantic 服务层，不做裸 ORM 插入）：
1. 创建维度层级「商家-档口」：逻辑维度 2(商家 seller_id) -> 14(档口 stall_id)；
2. 补齐核心指标结构化公式和分析关系：
   - 303 订单平均客单价 = 271 总GMV / 265 总订单数；
   - 总GMV、客户当日GMV及其订单数、商品件数、新增成交客户数关系；
   - 跨模型关系只允许预聚合后按日期、档口对齐；
3. 创建和更新指标关系（原有关系继续保留）：
   - 265 总订单数      CERTIFIED_DRIVER / SAME_DIRECTION / POSITIVE
   - 266 销售商品件数 CERTIFIED_DRIVER / SAME_DIRECTION / POSITIVE
   - 303 订单平均客单价 GOVERNED_ANALYSIS_RELATION / SAME_DIRECTION / POSITIVE
   并补充总商品件数、客户当日GMV、新增成交客户数等关系；
4. 为可加指标在已证明安全的商家、档口、客户、交易渠道维度追加 CONTRIBUTION；
5. 数据集资产选择：层级 + 全部核心指标关系；同时修复数据集存量问题
   （query_config 携带已移除的 semanticEnforcement；model_configs 缺少唯一默认模型，
   以 GMV 所在事实模型 246 为默认）；
6. 发布数据集契约、重建检索索引并输出验收核对结果。

脚本可重复执行：不会重复创建同名层级、指标关系或重复追加 CONTRIBUTION 用途。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session, col, select  # noqa: E402

from apps.retrieval.indexing.worker import process_index_jobs  # noqa: E402
from apps.retrieval.sources.semantic_indexing import (  # noqa: E402
    SemanticIndexCoordinator,
)
from apps.semantic.composition import (  # noqa: E402
    build_semantic_contract_publication_service,
    build_semantic_contract_service,
)
from apps.semantic.models.dto import (  # noqa: E402
    DatasetSchema,
    MetricFormulaComponent,
    MetricFormulaDefinition,
    MetricPayload,
    ModelRelationPayload,
)
from apps.semantic.models.dto.dataset import (  # noqa: E402
    DatasetAssetPayload,
    DatasetModelConfigPayload,
    DatasetPayload,
)
from apps.semantic.models.dto.semantic_contract import (  # noqa: E402
    DimensionHierarchyLevelPayload,
    DimensionHierarchyPayload,
    MetricDimensionCapabilityPayload,
    MetricRelationshipPayload,
)
from apps.semantic.models.orm import (  # noqa: E402
    MetricDimensionCapability,
    SemanticDataset,
    SemanticDatasetAsset,
    SemanticDatasetModelConfig,
    SemanticMetric,
    SemanticModelRelation,
)
from apps.semantic.models.orm.contract_version import (  # noqa: E402
    SemanticContractVersion,
)
from apps.semantic.repository.sqlmodel.dataset_index_repository import (  # noqa: E402
    SqlModelDatasetIndexRepository,
)
from apps.semantic.repository.sqlmodel.dataset_repository import (  # noqa: E402
    SqlModelDatasetRepository,
)
from apps.semantic.repository.sqlmodel.domain_repository import (  # noqa: E402
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.metric_repository import (  # noqa: E402
    SqlModelMetricRepository,
)
from apps.semantic.repository.sqlmodel.model_relation_repository import (  # noqa: E402
    SqlModelModelRelationRepository,
)
from apps.semantic.repository.sqlmodel.model_repository import (  # noqa: E402
    SqlModelModelRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import (  # noqa: E402
    SemanticSchemaLoader,
)
from apps.semantic.services.contract_publication_service import (  # noqa: E402
    SemanticContractCompletenessReport,
)
from apps.semantic.services.dataset_index_service import (  # noqa: E402
    SemanticDatasetIndexService,
)
from apps.semantic.services.dataset_service import SemanticDatasetService  # noqa: E402
from apps.semantic.services.metric_service import SemanticMetricService  # noqa: E402
from apps.semantic.services.model_relation_service import (  # noqa: E402
    SemanticModelRelationService,
)
from apps.semantic.services.schema_service import SemanticSchemaService  # noqa: E402
from common.core.db import engine  # noqa: E402

OID = 1
DATASET_ID = 243
DOMAIN_ID = 244

HIERARCHY_NAME = "seller_stall_hierarchy"
HIERARCHY_LEVELS = [(2, 1), (14, 2)]  # 商家(seller_id) -> 档口(stall_id)

TARGET_GMV = 271
ORDER_MODEL_ID = 246
CUSTOMER_MODEL_ID = 249
AOV_METRIC_ID = 303

# 关系只表达已经审定的分析范围，不将普通相关性写成因果关系。
RELATIONSHIPS = [
    # target, driver, relationship_type, validation_method, expected_direction,
    # common_dimensions, relation_path_key
    (271, 265, "CERTIFIED_DRIVER", "SAME_DIRECTION", "POSITIVE", [14, 2], None),
    (271, 266, "CERTIFIED_DRIVER", "SAME_DIRECTION", "POSITIVE", [14, 2], None),
    (271, 268, "CERTIFIED_DRIVER", "SAME_DIRECTION", "POSITIVE", [14, 2], None),
    (271, 303, "GOVERNED_ANALYSIS_RELATION", "SAME_DIRECTION", "POSITIVE", [14, 2], None),
    (271, 286, "GOVERNED_ANALYSIS_RELATION", "FORMULA_RECONCILIATION", "UNKNOWN", [14, 2], "order_customer"),
    (271, 313, "GOVERNED_ANALYSIS_RELATION", "SAME_DIRECTION", "POSITIVE", [14, 2], "order_customer"),
    (286, 287, "CERTIFIED_DRIVER", "SAME_DIRECTION", "POSITIVE", [2, 4, 14, 15], None),
    (286, 288, "CERTIFIED_DRIVER", "SAME_DIRECTION", "POSITIVE", [2, 4, 14, 15], None),
    (286, 313, "GOVERNED_ANALYSIS_RELATION", "SAME_DIRECTION", "POSITIVE", [2, 4, 14, 15], None),
]
LEGACY_ORDER_DRIVER = 263
SUPPORTED_TIME_ROLES = ["current", "previous"]

CONTRIBUTION_TARGETS = {
    265: [2, 14],
    268: [2, 14],
    271: [2, 14],
    286: [2, 4, 14, 15],
    287: [2, 4, 14, 15],
    288: [2, 4, 14, 15],
}

DEFAULT_MODEL_ID = 246  # 总GMV 所在事实模型


def _ensure_aov_formula(session: Session) -> None:
    metric = session.get(SemanticMetric, AOV_METRIC_ID)
    if metric is None or metric.oid != OID or metric.status != 1:
        raise SystemExit(f"metric {AOV_METRIC_ID} not found")
    expected = {
        "operation": "RATIO",
        "components": [
            {"metric_id": 271, "role": "numerator"},
            {"metric_id": 265, "role": "denominator"},
        ],
    }
    if metric.formula_definition == expected:
        print(f"[skip] metric {AOV_METRIC_ID} structured formula exists")
        return
    payload = MetricPayload(
        model_id=metric.model_id,
        name=metric.name,
        biz_name=metric.biz_name,
        description=metric.description,
        alias=list(metric.alias or []),
        default_agg=metric.default_agg,
        type=metric.type,
        define_type=metric.define_type,
        type_params=dict(metric.type_params or {}),
        relate_dimensions=list(metric.relate_dimensions or []),
        result_grain=list(metric.result_grain or []),
        additivity=metric.additivity,
        distinct_keys=list(metric.distinct_keys or []),
        time_semantics=metric.time_semantics,
        default_time_dimension_id=metric.default_time_dimension_id,
        snapshot_aggregation=metric.snapshot_aggregation,
        formula_definition=MetricFormulaDefinition(
            operation="RATIO",
            components=[
                MetricFormulaComponent(metric_id=271, role="numerator"),
                MetricFormulaComponent(metric_id=265, role="denominator"),
            ],
        ),
        comparison_grains=list(metric.comparison_grains or []),
        time_alignment_policy=metric.time_alignment_policy,
    )
    updated = SemanticMetricService(
        SqlModelMetricRepository(session),
        SqlModelModelRepository(session),
    ).update_metric(OID, AOV_METRIC_ID, payload)
    print(
        f"[update] metric {updated.id} structured formula="
        f"{json.dumps(updated.formula_definition, ensure_ascii=False)}"
    )


def _ensure_order_customer_model_relation(session: Session) -> int:
    existing = session.exec(
        select(SemanticModelRelation).where(
            SemanticModelRelation.oid == OID,
            SemanticModelRelation.left_model_id == ORDER_MODEL_ID,
            SemanticModelRelation.right_model_id == CUSTOMER_MODEL_ID,
            SemanticModelRelation.status == 1,
        )
    ).first()
    payload = ModelRelationPayload(
        domain_id=DOMAIN_ID,
        left_model_id=ORDER_MODEL_ID,
        right_model_id=CUSTOMER_MODEL_ID,
        join_type="left join",
        join_conditions=[
            {"leftField": "stat_date", "operator": "=", "rightField": "stat_date"},
            {"leftField": "stall_id", "operator": "=", "rightField": "stall_id"},
        ],
        cardinality="ONE_TO_MANY",
        left_unique=True,
        right_unique=False,
        metric_propagation="LEFT_TO_RIGHT",
        aggregation_safety="PRE_AGGREGATE_REQUIRED",
    )
    service = SemanticModelRelationService(
        SqlModelModelRelationRepository(session),
        SqlModelModelRepository(session),
    )
    if existing is None:
        relation = service.create_model_relation(OID, payload)
        print(f"[create] model relation {ORDER_MODEL_ID}->{CUSTOMER_MODEL_ID} id={relation.id}")
        return relation.id
    comparable = {
        key: getattr(existing, key)
        for key in (
            "domain_id",
            "left_model_id",
            "right_model_id",
            "join_type",
            "join_conditions",
            "ext",
            "cardinality",
            "left_unique",
            "right_unique",
            "metric_propagation",
            "aggregation_safety",
            "valid_time_condition",
        )
    }
    if comparable == payload.model_dump():
        print(f"[skip] model relation {ORDER_MODEL_ID}->{CUSTOMER_MODEL_ID} id={existing.id}")
        return existing.id
    relation = service.update_model_relation(OID, existing.id, payload)
    print(f"[update] model relation {ORDER_MODEL_ID}->{CUSTOMER_MODEL_ID} id={relation.id}")
    return relation.id


def _ensure_hierarchy(service) -> int:
    existing = [
        item
        for item in service.list_dimension_hierarchies(OID, DOMAIN_ID)
        if item.name == HIERARCHY_NAME
    ]
    if existing:
        print(f"[skip] hierarchy {HIERARCHY_NAME} exists id={existing[0].id}")
        return existing[0].id
    dto = service.create_dimension_hierarchy(
        OID,
        DimensionHierarchyPayload(
            domain_id=DOMAIN_ID,
            name=HIERARCHY_NAME,
            biz_name="商家-档口层级",
            description="Research 第三阶段验收：商家->档口固定级别下钻层级",
            levels=[
                DimensionHierarchyLevelPayload(
                    logical_dimension_id=dim_id, level_order=order
                )
                for dim_id, order in HIERARCHY_LEVELS
            ],
        ),
    )
    print(f"[create] hierarchy {dto.name} id={dto.id} levels={HIERARCHY_LEVELS}")
    return dto.id


def _ensure_relationships(
    service,
    existing_by_pair: dict[tuple[int, int], object],
    relation_paths: dict[str, list[int]],
) -> list[int]:
    relationship_ids: list[int] = []
    for (
        target_id,
        driver_id,
        rel_type,
        validation_method,
        expected_direction,
        logical_dimension_ids,
        relation_path_key,
    ) in RELATIONSHIPS:
        payload = MetricRelationshipPayload(
            domain_id=DOMAIN_ID,
            target_metric_id=target_id,
            driver_metric_id=driver_id,
            relationship_type=rel_type,
            validation_method=validation_method,
            expected_direction=expected_direction,
            supported_time_roles=SUPPORTED_TIME_ROLES,
            logical_dimension_ids=logical_dimension_ids,
            relation_path=relation_paths.get(relation_path_key or "", []),
        )
        existing = existing_by_pair.get((target_id, driver_id))
        if existing is not None:
            current = {
                "domain_id": existing.domain_id,
                "target_metric_id": existing.target_metric_id,
                "driver_metric_id": existing.driver_metric_id,
                "relationship_type": existing.relationship_type,
                "validation_method": existing.validation_method,
                "expected_direction": existing.expected_direction,
                "supported_time_roles": list(existing.supported_time_roles),
                "logical_dimension_ids": list(existing.logical_dimension_ids),
                "relation_path": list(existing.relation_path),
            }
            if current == payload.model_dump():
                print(
                    f"[skip] relationship id={existing.id} target={target_id} "
                    f"driver={driver_id} type={rel_type}"
                )
                relationship_ids.append(existing.id)
                continue
            dto = service.update_metric_relationship(OID, existing.id, payload)
            print(
                f"[update] relationship id={dto.id} target={target_id} "
                f"driver={driver_id} type={rel_type}"
            )
            relationship_ids.append(dto.id)
            continue
        legacy = existing_by_pair.get((target_id, LEGACY_ORDER_DRIVER))
        if target_id == TARGET_GMV and driver_id == 265 and legacy is not None:
            dto = service.update_metric_relationship(
                OID,
                legacy.id,
                payload,
            )
            print(
                f"[update] relationship id={dto.id} driver="
                f"{LEGACY_ORDER_DRIVER}->{driver_id} type={rel_type}"
            )
            relationship_ids.append(dto.id)
            continue
        dto = service.create_metric_relationship(OID, payload)
        print(
            f"[create] relationship id={dto.id} target={target_id} "
            f"driver={driver_id} type={rel_type}"
        )
        relationship_ids.append(dto.id)
    return relationship_ids


def _ensure_contribution(service, capabilities: list[MetricDimensionCapability]) -> None:
    for cap in capabilities:
        usages = list(cap.usages or [])
        if "CONTRIBUTION" in usages:
            print(f"[skip] capability id={cap.id} already has CONTRIBUTION")
            continue
        payload = MetricDimensionCapabilityPayload(
            metric_id=cap.metric_id,
            logical_dimension_id=cap.logical_dimension_id,
            usages=[*usages, "CONTRIBUTION"],
            binding_strategy=cap.binding_strategy,
            relation_path=list(cap.relation_path or []),
            target_model_id=cap.target_model_id,
            physical_dimension_id=cap.physical_dimension_id,
            aggregation_safety=cap.aggregation_safety,
            pre_aggregation_grain=list(cap.pre_aggregation_grain or []),
            time_alignment_policy=cap.time_alignment_policy,
            contribution_tolerance=cap.contribution_tolerance,
        )
        updated = service.update_metric_dimension_capability(OID, cap.id, payload)
        print(
            f"[update] capability id={updated.id} "
            f"metric={updated.metric_id} dim={updated.logical_dimension_id} "
            f"usages={updated.usages} v{updated.version}"
        )


def _update_dataset_selections(session: Session, hierarchy_id: int) -> None:
    dataset = session.get(SemanticDataset, DATASET_ID)
    if dataset is None:
        raise SystemExit(f"dataset {DATASET_ID} not found")

    configs = session.exec(
        select(SemanticDatasetModelConfig).where(
            SemanticDatasetModelConfig.oid == OID,
            SemanticDatasetModelConfig.dataset_id == DATASET_ID,
            SemanticDatasetModelConfig.status == 1,
        )
    ).all()
    existing_assets = session.exec(
        select(SemanticDatasetAsset).where(
            SemanticDatasetAsset.oid == OID,
            SemanticDatasetAsset.dataset_id == DATASET_ID,
            SemanticDatasetAsset.status == 1,
        )
    ).all()

    selected_keys = {(a.asset_type, a.asset_id) for a in existing_assets}
    assets = [
        DatasetAssetPayload(asset_type=a.asset_type, asset_id=a.asset_id, model_id=a.model_id)
        for a in sorted(existing_assets, key=lambda x: x.sort_order)
    ]
    for asset_type, asset_id in (
        [("DIMENSION_HIERARCHY", hierarchy_id)]
        + [("METRIC_RELATIONSHIP", rid) for rid in _relationship_ids(session)]
    ):
        if (asset_type, asset_id) in selected_keys:
            print(f"[skip] dataset asset {asset_type}:{asset_id} already selected")
            continue
        assets.append(DatasetAssetPayload(asset_type=asset_type, asset_id=asset_id))
        print(f"[select] dataset asset {asset_type}:{asset_id}")

    model_config_payloads = [
        DatasetModelConfigPayload(
            model_id=c.model_id,
            includes_all=c.includes_all,
            is_default=c.is_default or c.model_id == DEFAULT_MODEL_ID,
            sort_order=c.sort_order,
        )
        for c in sorted(configs, key=lambda x: x.sort_order)
    ]
    fixed_default = sum(item.is_default for item in model_config_payloads)
    if fixed_default != 1:
        raise SystemExit(f"default model fix failed: {fixed_default} defaults")

    legacy_query_config = dict(dataset.query_config or {})
    if "semanticEnforcement" in legacy_query_config:
        print("[fix] drop legacy query_config.semanticEnforcement")
    legacy_query_config.pop("semanticEnforcement", None)

    payload = DatasetPayload(
        domain_id=dataset.domain_id,
        name=dataset.name,
        biz_name=dataset.biz_name,
        description=dataset.description,
        alias=list(dataset.alias or []),
        modelConfigs=model_config_payloads,
        assets=assets,
        query_config=legacy_query_config,
        owner=dataset.owner,
        default_timezone=dataset.default_timezone,
        calendar_type=dataset.calendar_type,
        week_start_day=dataset.week_start_day,
        fiscal_year_start_month=dataset.fiscal_year_start_month,
        holiday_calendar_key=dataset.holiday_calendar_key,
    )
    updated = SemanticDatasetService(
        SqlModelDatasetRepository(session),
        SqlModelDomainRepository(session),
    ).update_dataset(OID, DATASET_ID, payload)
    print(
        f"[update] dataset {DATASET_ID} schema_version={updated.schema_version} "
        f"contract_version={updated.contract_version}(待发布)"
    )


def _relationship_ids(session: Session) -> list[int]:
    return [row.id for row in _relationship_rows(session)]


def _relationship_dtos_by_pair(service) -> dict[tuple[int, int], object]:
    expected_pairs = {(item[0], item[1]) for item in RELATIONSHIPS}
    expected_pairs.add((TARGET_GMV, LEGACY_ORDER_DRIVER))
    return {
        (item.target_metric_id, item.driver_metric_id): item
        for item in service.list_metric_relationships(OID, DOMAIN_ID)
        if (item.target_metric_id, item.driver_metric_id) in expected_pairs
    }


def _relationship_rows(session: Session) -> list:
    from apps.semantic.models.orm import MetricRelationship

    return list(
        session.exec(
            select(MetricRelationship).where(
                MetricRelationship.oid == OID,
                col(MetricRelationship.target_metric_id).in_(
                    sorted({item[0] for item in RELATIONSHIPS})
                ),
                col(MetricRelationship.driver_metric_id).in_(
                    sorted({item[1] for item in RELATIONSHIPS})
                ),
                MetricRelationship.status == 1,
            )
        ).all()
    )


def _publish(session: Session) -> SemanticContractCompletenessReport:
    report = build_semantic_contract_publication_service(session).publish_dataset(
        OID,
        DATASET_ID,
        SemanticSchemaLoader(session),
    )
    return report


def _rebuild_index(session: Session) -> None:
    result = SemanticDatasetIndexService(
        SqlModelDatasetIndexRepository(session),
        SemanticSchemaService(SemanticSchemaLoader(session)),
        SemanticIndexCoordinator(session),
    ).rebuild_index(OID, DATASET_ID)
    job_results = process_index_jobs(result.job_ids)
    failed = [item for item in job_results if item.status.lower() != "succeeded"]
    if failed:
        raise SystemExit(f"semantic index rebuild failed: {failed}")
    print(
        f"[index] dataset={DATASET_ID} index_version={result.index_version} "
        f"jobs={list(result.job_ids)}"
    )


def _verify(session: Session, report: SemanticContractCompletenessReport) -> None:
    print("\n=== 验收核对 ===")
    print(f"report.status = {report.status}")
    failing = [c for c in report.checks if c.status == "FAIL"]
    print(f"failing checks = {len(failing)}")
    for check in failing[:20]:
        print(
            f"  FAIL {check.check_type} {check.subject_refs} "
            f"{check.reason_code}: {check.message}"
        )
    if report.status != "READY":
        return

    dataset = session.get(SemanticDataset, DATASET_ID)
    print(
        f"dataset.schema_version = {dataset.schema_version} "
        f"(发布前为 12)\n"
        f"dataset.contract_version = {dataset.contract_version} "
        f"(发布前为 1)"
    )
    version = session.exec(
        select(SemanticContractVersion)
        .where(
            SemanticContractVersion.oid == OID,
            SemanticContractVersion.dataset_id == DATASET_ID,
        )
        .order_by(col(SemanticContractVersion.contract_version).desc())
        .limit(1)
    ).one()
    schema = DatasetSchema.model_validate(version.asset_snapshot["schema"])
    hierarchies = schema.dimension_hierarchies
    relationships = schema.research_relationships
    contrib_caps = [
        c
        for c in schema.metric_dimension_capabilities
        if "CONTRIBUTION" in (c.get("usages") or [])
    ]
    print(
        f"published contract_version = {version.contract_version}\n"
        f"schema_fingerprint = {schema.schema_fingerprint[:16]}...\n"
        f"dimension_hierarchies = {len(hierarchies)}\n"
        f"research_relationships = {len(relationships)}\n"
        f"CONTRIBUTION capabilities = {len(contrib_caps)}"
    )
    for h in hierarchies:
        print(
            f"  hierarchy id={h.id} status={h.contract_status} levels="
            f"{[(lv.logical_dimension_id, lv.level_order) for lv in h.levels]}"
        )
    for r in relationships:
        print(
            f"  relationship target={r.target_metric_ref} "
            f"driver={r.driver_metric_ref} type={r.relationship_type} "
            f"dims={list(r.dimension_refs)} roles={list(r.time_roles)}"
        )
    assert len(hierarchies) >= 1, "hierarchies must be published"
    assert len(relationships) >= len(RELATIONSHIPS), "relationships must be published"
    expected_contribution_count = sum(len(items) for items in CONTRIBUTION_TARGETS.values())
    assert len(contrib_caps) >= expected_contribution_count, (
        "CONTRIBUTION capabilities must be published"
    )
    print("\n全部断言通过：治理资产已进入发布契约。")


def main() -> None:
    with Session(engine) as session:
        contract_service = build_semantic_contract_service(session)

        _ensure_aov_formula(session)
        model_relation_id = _ensure_order_customer_model_relation(session)
        hierarchy_id = _ensure_hierarchy(contract_service)

        existing_relationships = _relationship_dtos_by_pair(contract_service)
        _ensure_relationships(
            contract_service,
            existing_relationships,
            {"order_customer": [model_relation_id]},
        )

        contribution_pairs = [
            (metric_id, logical_id)
            for metric_id, logical_ids in CONTRIBUTION_TARGETS.items()
            for logical_id in logical_ids
        ]
        caps = session.exec(
            select(MetricDimensionCapability).where(
                MetricDimensionCapability.oid == OID,
                col(MetricDimensionCapability.metric_id).in_(
                    sorted(CONTRIBUTION_TARGETS)
                ),
                MetricDimensionCapability.status == 1,
            )
        ).all()
        caps = [
            item
            for item in caps
            if (item.metric_id, item.logical_dimension_id) in contribution_pairs
        ]
        if len(caps) != len(contribution_pairs):
            raise SystemExit(
                f"expected {len(contribution_pairs)} capability rows, found {len(caps)}"
            )
        _ensure_contribution(contract_service, caps)

        session.expire_all()  # 服务层各自 commit，刷新后再读最新状态
        _update_dataset_selections(session, hierarchy_id)

        session.expire_all()
        report = _publish(session)

        session.expire_all()
        _verify(session, report)
        if report.status == "READY":
            _rebuild_index(session)


if __name__ == "__main__":
    main()
