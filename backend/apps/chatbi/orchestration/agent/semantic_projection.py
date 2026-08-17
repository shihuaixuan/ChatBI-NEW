"""Agent 并发工具结果的语义计划投影。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from apps.retrieval import (
    RetrievalBundle,
    RetrievalRequest,
    bind_default_time_dimensions,
    bundle_to_semantic_payload,
    is_compilation_decision_executable,
)
from apps.semantic import DatasetSchema
from apps.tool.tools.semantic import project_semantic_package
from apps.tool.tools.semantic_contracts import (
    project_semantic_compile_plan,
    project_semantic_query_plans,
)


def refresh_semantic_projection(
    state: dict[str, Any],
    raw_package: dict[str, Any],
    raw_scope: dict[str, Any],
    *,
    schema_data: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """把已经确认的时间结果补入语义检索产生的可信查询计划。"""

    understanding = state.get("question_understanding")
    intent = understanding.get("intent") if isinstance(understanding, dict) else None
    time_range = intent.get("time_range") if isinstance(intent, dict) else None
    normalized = time_range.get("normalized") if isinstance(time_range, dict) else None
    if not isinstance(normalized, dict) or normalized.get("kind") != "absolute_range":
        return raw_package, raw_scope, {}

    package = deepcopy(raw_package)
    scope = deepcopy(raw_scope)
    snapshot_patch: dict[str, Any] = {}

    # 语义检索和时间解析可以并发完成。时间结果回来后，必须基于检索快照重新绑定
    # 指标模型的默认时间维度，否则只有 normalized_time_range，没有可编译的时间过滤。
    raw_bundle = state.get("semantic_bundle")
    raw_request = state.get("semantic_retrieval_request")
    if isinstance(raw_bundle, dict) and isinstance(raw_request, dict) and isinstance(
        schema_data, dict
    ):
        request = RetrievalRequest.model_validate(raw_request)
        request_time_range = dict(request.intent.time_range)
        request_time_range.update(
            {
                "value_status": "provided",
                "normalized": normalized,
            }
        )
        request = request.model_copy(
            update={
                "intent": request.intent.model_copy(
                    update={"time_range": request_time_range}
                )
            }
        )
        schema = DatasetSchema.model_validate(schema_data)
        bundle = bind_default_time_dimensions(
            request,
            RetrievalBundle.model_validate(raw_bundle),
            schema,
        )
        payload = bundle_to_semantic_payload(
            request,
            bundle,
            schema,
        )
        authorized_tables = {
            str(table).lower()
            for table in scope.get("authorized_tables") or []
            if str(table).strip()
        }
        package = project_semantic_package(payload, authorized_tables)
        package = package.model_dump(mode="json")
        scope["decision_status"] = bundle.decision.status.value
        scope["allowed_assets"] = [
            item.model_dump(mode="json")
            for item in bundle.decision.allowed_asset_ids
        ]
        snapshot_patch = {
            "semantic_bundle": bundle.model_dump(mode="json"),
            "semantic_payload": payload,
            "semantic_retrieval_request": request.model_dump(mode="json"),
        }

    slot_bindings = package.get("slot_bindings")
    if not isinstance(slot_bindings, dict):
        return raw_package, raw_scope, snapshot_patch
    slot_bindings = deepcopy(slot_bindings)
    time_filters = slot_bindings.get("time_filters")
    time_dimensions = slot_bindings.get("time_dimensions")
    if not isinstance(time_filters, list) or not isinstance(time_dimensions, list):
        return raw_package, raw_scope, snapshot_patch
    if not time_filters and time_dimensions:
        selected_time_dimension = time_dimensions[0]
        if isinstance(selected_time_dimension, dict):
            time_filter = dict(selected_time_dimension)
            time_filter.update(
                {
                    "operator": "=",
                    "value": normalized,
                    "source": "intent_time_range",
                }
            )
            slot_bindings["time_filters"] = [time_filter]
            slot_bindings["filters"] = [
                *(
                    slot_bindings.get("value_filters")
                    if isinstance(slot_bindings.get("value_filters"), list)
                    else []
                ),
                *(
                    slot_bindings.get("dimension_filters")
                    if isinstance(slot_bindings.get("dimension_filters"), list)
                    else []
                ),
                time_filter,
            ]
    package["slot_bindings"] = slot_bindings
    scope["normalized_time_range"] = normalized
    scope["compile_plan"] = project_semantic_compile_plan(
        slot_bindings,
        intent if isinstance(intent, dict) else {},
    ).model_dump(mode="json")

    # 时间结果回投影也必须遵守检索决策门控，歧义状态只更新候选快照，不能生成严格计划。
    if (
        scope.get("semantic_enforcement") == "STRICT"
        and is_compilation_decision_executable(scope.get("decision_status"))
        and isinstance(schema_data, dict)
    ):
        schema = DatasetSchema.model_validate(schema_data)
        query_plans = project_semantic_query_plans(
            schema,
            slot_bindings,
            intent if isinstance(intent, dict) else {},
            package.get("multi_query_plans")
            if isinstance(package.get("multi_query_plans"), list)
            else None,
        )
        scope["query_plan"] = query_plans[0][0].model_dump(mode="json")
        scope["query_plans"] = [
            query_plan.model_dump(mode="json") for query_plan, _ in query_plans
        ]
        scope["validation_report"] = query_plans[0][1].model_dump(mode="json")
        scope["validation_reports"] = [
            validation_report.model_dump(mode="json")
            for _, validation_report in query_plans
        ]
    return package, scope, snapshot_patch


__all__ = ["refresh_semantic_projection"]
