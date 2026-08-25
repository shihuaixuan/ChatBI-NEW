"""检查 dataset 243 核心指标分析关系和维度能力覆盖。

该脚本只读取已发布 DatasetSchema，不读取草稿资产。发现缺口时返回非零退出码，
用于发布验收和后续治理巡检。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session  # noqa: E402

from apps.semantic.repository.sqlmodel.schema_loader import (  # noqa: E402
    SemanticSchemaLoader,
)
from apps.semantic.services.schema_service import SemanticSchemaService  # noqa: E402
from common.core.db import engine  # noqa: E402

OID = 1
DATASET_ID = 243

EXPECTED_RELATIONSHIPS = {
    ("METRIC:271:246", "METRIC:265:246"),
    ("METRIC:271:246", "METRIC:266:246"),
    ("METRIC:271:246", "METRIC:268:246"),
    ("METRIC:271:246", "METRIC:303:246"),
    ("METRIC:271:246", "METRIC:286:249"),
    ("METRIC:271:246", "METRIC:313:249"),
    ("METRIC:286:249", "METRIC:287:249"),
    ("METRIC:286:249", "METRIC:288:249"),
    ("METRIC:286:249", "METRIC:313:249"),
}

EXPECTED_CONTRIBUTION_CAPABILITIES = {
    (265, 2),
    (265, 14),
    (268, 2),
    (268, 14),
    (271, 2),
    (271, 14),
    (286, 2),
    (286, 4),
    (286, 14),
    (286, 15),
    (287, 2),
    (287, 4),
    (287, 14),
    (287, 15),
    (288, 2),
    (288, 4),
    (288, 14),
    (288, 15),
}


def evaluate_coverage(schema: Any) -> dict[str, Any]:
    """返回稳定的覆盖报告，缺失项使用业务资产标识表达。"""

    relationships = {
        (item.target_metric_ref, item.driver_metric_ref): item
        for item in schema.research_relationships
        if item.relationship_type != "formula_component"
    }
    missing_relationships = sorted(EXPECTED_RELATIONSHIPS - relationships.keys())
    invalid_relationships = sorted(
        pair
        for pair in EXPECTED_RELATIONSHIPS & relationships.keys()
        if not relationships[pair].dimension_refs or not relationships[pair].time_roles
    )

    contribution_capabilities = {
        (int(item["metric_id"]), int(item["logical_dimension_id"]))
        for item in schema.metric_dimension_capabilities
        if "CONTRIBUTION" in (item.get("usages") or [])
        and item.get("aggregation_safety") != "FORBIDDEN"
    }
    missing_contribution = sorted(
        EXPECTED_CONTRIBUTION_CAPABILITIES - contribution_capabilities
    )

    aov_contract = next(
        (
            item
            for item in schema.metric_contracts
            if int(item.get("metric_id") or 0) == 303
        ),
        {},
    )
    formula = aov_contract.get("formula_definition") or {}
    formula_components = {
        (int(item.get("metric_id") or 0), str(item.get("role") or ""))
        for item in formula.get("components") or []
        if isinstance(item, dict)
    }
    formula_valid = formula.get("operation") == "RATIO" and formula_components == {
        (271, "numerator"),
        (265, "denominator"),
    }

    model_relation_valid = any(
        int(item.get("left_model_id") or 0) == 246
        and int(item.get("right_model_id") or 0) == 249
        and item.get("contract_status") == "READY"
        and item.get("aggregation_safety") == "PRE_AGGREGATE_REQUIRED"
        and item.get("metric_propagation") == "LEFT_TO_RIGHT"
        for item in schema.relation_contracts
    )

    issues: list[dict[str, Any]] = []
    if missing_relationships:
        issues.append(
            {"type": "MISSING_RELATIONSHIP", "items": missing_relationships}
        )
    if invalid_relationships:
        issues.append(
            {"type": "INCOMPLETE_RELATIONSHIP", "items": invalid_relationships}
        )
    if missing_contribution:
        issues.append(
            {"type": "MISSING_CONTRIBUTION", "items": missing_contribution}
        )
    if not formula_valid:
        issues.append({"type": "AOV_FORMULA_INVALID", "metric_id": 303})
    if not model_relation_valid:
        issues.append(
            {"type": "ORDER_CUSTOMER_RELATION_INVALID", "models": [246, 249]}
        )

    return {
        "status": "READY" if not issues else "INCOMPLETE",
        "dataset_id": schema.data_set.id,
        "schema_version": schema.schema_version,
        "contract_version": schema.contract_version,
        "schema_fingerprint": schema.schema_fingerprint,
        "relationship_coverage": {
            "expected": len(EXPECTED_RELATIONSHIPS),
            "covered": len(EXPECTED_RELATIONSHIPS) - len(missing_relationships),
        },
        "contribution_coverage": {
            "expected": len(EXPECTED_CONTRIBUTION_CAPABILITIES),
            "covered": len(EXPECTED_CONTRIBUTION_CAPABILITIES)
            - len(missing_contribution),
        },
        "aov_formula_valid": formula_valid,
        "order_customer_relation_valid": model_relation_valid,
        "issues": issues,
    }


def main() -> None:
    with Session(engine) as session:
        schema = SemanticSchemaService(SemanticSchemaLoader(session)).get_dataset_schema(
            OID,
            DATASET_ID,
        )
    report = evaluate_coverage(schema)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "READY":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
