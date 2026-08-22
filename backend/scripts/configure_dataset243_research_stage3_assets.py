"""配置并发布 dataset 243 的 Research 第三阶段验收治理资产。

按已确认的验收方案执行（全部走 Semantic 服务层，不做裸 ORM 插入）：
1. 创建维度层级「商家-档口」：逻辑维度 2(商家 seller_id) -> 14(档口 stall_id)；
2. 创建 3 条指标关系（目标指标 271 总GMV）：
   - 265 总订单数      CERTIFIED_DRIVER / SAME_DIRECTION / POSITIVE
   - 266 销售商品件数 CERTIFIED_DRIVER / SAME_DIRECTION / POSITIVE
   - 303 订单平均客单价 GOVERNED_ANALYSIS_RELATION / SAME_DIRECTION / POSITIVE
   共同分析维度 [14, 2]，支持时间角色 [current, previous]；
3. 为能力行 271x14、271x2 追加 CONTRIBUTION 用途；
4. 数据集资产选择：层级 + 3 条关系；同时修复数据集存量问题
   （query_config 携带已移除的 semanticEnforcement；model_configs 缺少唯一默认模型，
   以 GMV 所在事实模型 246 为默认）；
5. 发布数据集契约并输出验收核对结果。

脚本幂等：已存在的同名层级/同目标驱动关系/已含 CONTRIBUTION 的能力会被跳过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session, col, select  # noqa: E402

from apps.semantic.composition import (  # noqa: E402
    build_semantic_contract_publication_service,
    build_semantic_contract_service,
)
from apps.semantic.models.dto import DatasetSchema  # noqa: E402
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
)
from apps.semantic.models.orm.contract_version import (  # noqa: E402
    SemanticContractVersion,
)
from apps.semantic.repository.sqlmodel.dataset_repository import (  # noqa: E402
    SqlModelDatasetRepository,
)
from apps.semantic.repository.sqlmodel.domain_repository import (  # noqa: E402
    SqlModelDomainRepository,
)
from apps.semantic.repository.sqlmodel.schema_loader import (  # noqa: E402
    SemanticSchemaLoader,
)
from apps.semantic.services.contract_publication_service import (  # noqa: E402
    SemanticContractCompletenessReport,
)
from apps.semantic.services.dataset_service import SemanticDatasetService  # noqa: E402
from common.core.db import engine  # noqa: E402

OID = 1
DATASET_ID = 243
DOMAIN_ID = 244

HIERARCHY_NAME = "seller_stall_hierarchy"
HIERARCHY_LEVELS = [(2, 1), (14, 2)]  # 商家(seller_id) -> 档口(stall_id)

TARGET_GMV = 271
RELATIONSHIPS = [
    # (driver_metric_id, relationship_type)
    (265, "CERTIFIED_DRIVER"),  # 总订单数，与总GMV和总客单价口径一致
    (266, "CERTIFIED_DRIVER"),  # 销售商品件数
    (303, "GOVERNED_ANALYSIS_RELATION"),  # 订单平均客单价
]
LEGACY_ORDER_DRIVER = 263
RELATIONSHIP_DIMS = [14, 2]
SUPPORTED_TIME_ROLES = ["current", "previous"]

CONTRIBUTION_METRIC = 271
CONTRIBUTION_DIMS = [14, 2]

DEFAULT_MODEL_ID = 246  # 总GMV 所在事实模型


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


def _ensure_relationships(service, existing_by_driver: dict[int, int]) -> list[int]:
    created: list[int] = []
    for driver_id, rel_type in RELATIONSHIPS:
        if driver_id in existing_by_driver:
            print(f"[skip] relationship target={TARGET_GMV} driver={driver_id} exists")
            continue
        payload = MetricRelationshipPayload(
            domain_id=DOMAIN_ID,
            target_metric_id=TARGET_GMV,
            driver_metric_id=driver_id,
            relationship_type=rel_type,
            validation_method="SAME_DIRECTION",
            expected_direction="POSITIVE",
            supported_time_roles=SUPPORTED_TIME_ROLES,
            logical_dimension_ids=RELATIONSHIP_DIMS,
        )
        if driver_id == 265 and LEGACY_ORDER_DRIVER in existing_by_driver:
            dto = service.update_metric_relationship(
                OID,
                existing_by_driver[LEGACY_ORDER_DRIVER],
                payload,
            )
            print(
                f"[update] relationship id={dto.id} driver="
                f"{LEGACY_ORDER_DRIVER}->{driver_id} type={rel_type}"
            )
            created.append(dto.id)
            continue
        dto = service.create_metric_relationship(OID, payload)
        print(
            f"[create] relationship id={dto.id} target={TARGET_GMV} "
            f"driver={driver_id} type={rel_type}"
        )
        created.append(dto.id)
    return created


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


def _relationship_ids_by_driver(session: Session) -> dict[int, int]:
    from apps.semantic.models.orm import MetricRelationship

    driver_ids = [driver for driver, _ in RELATIONSHIPS] + [LEGACY_ORDER_DRIVER]
    rows = session.exec(
        select(MetricRelationship).where(
            MetricRelationship.oid == OID,
            MetricRelationship.target_metric_id == TARGET_GMV,
            col(MetricRelationship.driver_metric_id).in_(driver_ids),
            MetricRelationship.status == 1,
        )
    ).all()
    return {row.driver_metric_id: row.id for row in rows}


def _relationship_rows(session: Session) -> list:
    from apps.semantic.models.orm import MetricRelationship

    return list(
        session.exec(
            select(MetricRelationship).where(
                MetricRelationship.oid == OID,
                MetricRelationship.target_metric_id == TARGET_GMV,
                col(MetricRelationship.driver_metric_id).in_(
                    [driver for driver, _ in RELATIONSHIPS]
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
    assert len(relationships) >= 3, "relationships must be published"
    assert len(contrib_caps) >= 2, "CONTRIBUTION capabilities must be published"
    print("\n全部断言通过：治理资产已进入发布契约。")


def main() -> None:
    with Session(engine) as session:
        contract_service = build_semantic_contract_service(session)

        hierarchy_id = _ensure_hierarchy(contract_service)

        existing_relationships = _relationship_ids_by_driver(session)
        _ensure_relationships(contract_service, existing_relationships)

        caps = session.exec(
            select(MetricDimensionCapability).where(
                MetricDimensionCapability.oid == OID,
                MetricDimensionCapability.metric_id == CONTRIBUTION_METRIC,
                col(MetricDimensionCapability.logical_dimension_id).in_(
                    CONTRIBUTION_DIMS
                ),
                MetricDimensionCapability.status == 1,
            )
        ).all()
        if len(caps) != len(CONTRIBUTION_DIMS):
            raise SystemExit(
                f"expected {len(CONTRIBUTION_DIMS)} capability rows, found {len(caps)}"
            )
        _ensure_contribution(contract_service, caps)

        session.expire_all()  # 服务层各自 commit，刷新后再读最新状态
        _update_dataset_selections(session, hierarchy_id)

        session.expire_all()
        report = _publish(session)

        session.expire_all()
        _verify(session, report)


if __name__ == "__main__":
    main()
