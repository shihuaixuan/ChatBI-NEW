#!/usr/bin/env python3
"""测试“语义资产检索 -> 候选约束绑定 -> 逻辑查询校验”方案。

本脚本不测试问题理解模型，也不执行 SQL。脚本直接把自然语言问题送入底层
语义资产检索，测试重点是：

1. 语义层能否为问题召回可用的指标、维度、值和时间维度候选；
2. 候选资产是否以定义和稳定引用的形式传给后续模型；
3. 后续模型是否只引用候选资产，并生成可供执行阶段继续处理的逻辑查询。

运行前设置：

    export ORCA_API_KEY="你的 Orca API Key"
    python backend/scripts/test_semantic_retrieval_binding.py \
        --dataset-id 243 \
        --disable-rerank

如果项目使用的租户、用户或数据集不是默认值，可通过命令行参数覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# 允许从仓库根目录直接运行脚本时导入 backend 下的项目模块。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session  # noqa: E402

from apps.retrieval.models.dto import RetrievalPurpose, RetrievalSubQuery  # noqa: E402
from apps.retrieval.embedding import OpenAICompatibleEmbeddingProvider  # noqa: E402
from apps.retrieval.query.hybrid import (  # noqa: E402
    HybridRetrievalConfig,
    SemanticBindingSearchStore,
)
from apps.retrieval.query.service import build_semantic_binding_request  # noqa: E402
from apps.retrieval.query.semantic_runtime import (  # noqa: E402
    RetrievalEmbeddingRuntimeConfig,
)
from apps.semantic.composition import build_semantic_schema_service  # noqa: E402
from common.core.config import settings  # noqa: E402
from common.core.db import engine  # noqa: E402


API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
REASONING_EFFORT = "low"
ASSET_GROUPS = ("metrics", "dimensions", "values", "terms")
REF_PATTERN = re.compile(r"^(METRIC|DIMENSION|VALUE|TERM):([1-9][0-9]*)(?::([1-9][0-9]*))?$")

LOGICAL_QUERY_KEYS = {
    "status",
    "measures",
    "group_by",
    "filters",
    "time_filters",
    "order_by",
    "limit",
    "calculations",
    "unresolved",
}


# 这些案例覆盖 Fast、Plan、Research 常见的查询形态，但不把模式字段传给模型。
# 模式路由应由后续执行需求分析根据逻辑查询决定。
TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "fast_metric_group_time",
        "question": "2026年6月各店铺的总GMV是多少？",
        "expected": {
            "measures": {"gmv_total"},
            "group_by": {"stall_id"},
            "time_filters": {"stat_date"},
        },
    },
    {
        "name": "plan_metric_having_top_n",
        "question": "查询2026年6月总GMV高于10000元的店铺，并按GMV降序取前10名。",
        "expected": {
            "measures": {"gmv_total"},
            "group_by": {"stall_id"},
            "time_filters": {"stat_date"},
        },
    },
    {
        "name": "plan_multi_metric_filter_value",
        "question": "2026年6月店铺100023的总GMV和订单数是多少？",
        "expected": {
            "measures": {"gmv_total", "order_cnt_total"},
            "filters": {"stall_id"},
            "time_filters": {"stat_date"},
        },
    },
    {
        "name": "research_period_comparison",
        "question": "店铺100023在2026年6月相比5月的总GMV变化量和增长率是多少？",
        "expected": {
            "measures": {"gmv_total"},
            "filters": {"stall_id"},
            "time_filters": {"stat_date"},
            "calculations": {"difference", "growth_rate"},
        },
    },
]


SYSTEM_PROMPT = """你是问数系统中的“候选资产绑定器”。

你的任务不是重新理解全部业务，也不是生成 SQL，而是：

1. 阅读用户问题和语义层返回的候选资产；
2. 在候选资产中选择问题真正需要的指标、维度、维度值和时间维度；
3. 输出一个下游可以继续做执行需求分析的逻辑查询。

候选资产是唯一允许使用的语义事实。候选中没有的资产不能自行补充，不能依据常识
创造指标、维度、字段名或内部 ID。候选资产的 `payload`、`description`、`biz_name`
和 `name` 用来判断口径，`ref` 用来建立下游稳定引用。

输出必须是合法 JSON 对象，且只能包含以下字段：

{
  "status": "resolved|needs_clarification|missed",
  "measures": [{"ref": "METRIC:资产ID[:模型ID]"}],
  "group_by": [{"ref": "DIMENSION:资产ID[:模型ID]"}],
  "filters": [
    {
      "ref": "DIMENSION:资产ID[:模型ID] 或 METRIC:资产ID[:模型ID]",
      "operator": "=|!=|>|>=|<|<=|in|not_in|contains",
      "value": "筛选值或数值",
      "value_ref": "VALUE:资产ID[:模型ID]",
      "stage": "where|having"
    }
  ],
  "time_filters": [
    {
      "ref": "DIMENSION:时间维度资产ID[:模型ID]",
      "expression": "用户原话中的时间表达"
    }
  ],
  "order_by": [{"ref": "METRIC:资产ID[:模型ID] 或 DIMENSION:资产ID[:模型ID]", "direction": "asc|desc"}],
  "limit": 10,
  "calculations": [
    {
      "type": "difference|growth_rate|share|trend|attribution",
      "ref": "METRIC:资产ID[:模型ID]",
      "raw": "用户原话中的计算要求"
    }
  ],
  "unresolved": ["确实无法从候选资产和问题中确定的内容"]
}

字段含义：

- status：resolved 表示资产和查询结构已经明确；needs_clarification 表示候选中有多个
  不能安全区分的资产；missed 表示必要资产没有召回。
- measures：用户要查询或参与计算的指标。只能引用 asset_type 为 METRIC 的候选。
- group_by：用户明确要求分组或展示的维度。只能引用 DIMENSION 候选。
- filters：普通筛选和指标聚合后筛选。维度筛选使用 stage=where，指标阈值使用
  stage=having。`ref` 是被筛选目标，`value` 是用户问题中的筛选值；如果候选中有
  对应的 VALUE 资产，同时填写它的 value_ref。不要把 value 单独输出成没有归属的对象。
- time_filters：时间表达绑定到候选中的时间维度；只保留用户原话，不要在这里计算起止日期。
- order_by：明确要求的排序。limit 只填写用户明确要求的数量，没有 Top N 时为 null。
- calculations：明确要求的差值、增长率、占比、趋势或归因等后续计算，不要把计算结果
  虚构成新的指标资产。
- unresolved：只有无法安全绑定且会影响后续执行时才填写；没有时为空数组。

严格规则：

1. 所有 ref 和 value_ref 必须来自候选资产的 ref，不能编造。
2. 不能输出 asset_id、model_id 以外的内部结构；不能输出 SQL、模式、路由或执行计划。
3. 不要把普通维度筛选写成 group_by；不要把指标阈值写成 where。
4. 时间条件必须放在 time_filters，不能放到 filters。
5. 用户没有明确要求的指标、维度、排序、limit 和计算关系必须为空或 null。
6. 如果 status 不是 resolved，也不要猜测候选之外的资产。
7. measures、group_by、filters、time_filters、order_by、calculations 和 unresolved
   都必须是数组；没有内容时返回 []，禁止返回 null。
8. filters 中没有可绑定的 VALUE 候选时，只输出 value，不要输出 value_ref；禁止输出空字符串
   作为 value_ref。没有值候选时不能编造 VALUE 引用。
9. 只返回 JSON，不要返回 Markdown 代码块或解释文字。"""


def _is_time_dimension_payload(payload: dict[str, Any]) -> bool:
    """按语义资产定义识别时间维度，不依赖问题理解结果。"""

    ext_info = payload.get("ext_info")
    ext_info = ext_info if isinstance(ext_info, dict) else {}
    dimension_type = str(ext_info.get("dimension_type") or "").lower()
    semantic_type = str(ext_info.get("semantic_type") or "").lower()
    data_type = str(ext_info.get("dimension_data_type") or "").lower()
    return bool(
        ext_info.get("is_default_time")
        or ext_info.get("time_granularities")
        or dimension_type in {"time", "partition_time"}
        or semantic_type == "time"
        or any(token in data_type for token in ("date", "time", "timestamp"))
    )


def _raw_candidate_from_hit(
    hit: Any,
    definitions: dict[tuple[str, int], dict[str, Any]],
    recall_score: float | None = None,
) -> dict[str, Any] | None:
    """把底层召回命中转换为模型可读的候选资产。"""

    asset_ref = hit.asset_ref
    if asset_ref is None:
        return None
    asset_type = asset_ref.asset_type.value
    definition = definitions.get((asset_type, asset_ref.asset_id))
    if definition is None:
        return None
    scores = hit.scores.model_dump(mode="json")
    numeric_scores = [
        float(value)
        for value in scores.values()
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    # 召回对象上的 score 是混合检索排序分数；命中详情中的 scores
    # 可能没有保存当前检索通道的分数，因此优先使用召回对象分数。
    score = recall_score if recall_score is not None else max(numeric_scores, default=0.0)
    return {
        "source": "raw_question_retrieval",
        "asset_type": asset_type,
        "asset_id": asset_ref.asset_id,
        "model_id": asset_ref.model_id,
        "name": definition.get("name"),
        "display_name": definition.get("name"),
        "biz_name": definition.get("biz_name"),
        "description": definition.get("description"),
        "score": score,
        "matched_text": hit.matched_text,
        "matched_field": hit.matched_field,
        "retrieval_title": hit.title,
        "retrieval_scores": scores,
        "payload": definition,
    }


def retrieve_by_raw_question(
    session: Any,
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
    question: str,
    schema: Any,
) -> dict[str, Any]:
    """直接使用自然语言问题检索各类语义资产，不经过 intent_hints。"""

    # 这里只用 request 携带数据集、租户和权限范围；intent 不参与检索查询规划。
    request = build_semantic_binding_request(
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        original_question=question,
        rewritten_question=question,
        intent={"intent_type": "raw_question"},
    )
    embedding_config = RetrievalEmbeddingRuntimeConfig.from_settings(settings)
    embedding_api_key = (
        embedding_config.api_key
        or getattr(settings, "SILICONFLOW_API_KEY", "")
    )
    embedding_provider = None
    if embedding_config.enabled and embedding_api_key.strip():
        embedding_provider = OpenAICompatibleEmbeddingProvider(
            api_base_url=embedding_config.api_base_url,
            api_key=embedding_api_key,
            model=embedding_config.model,
            dimension=embedding_config.dimension,
        )
    # exact、alias、lexical 始终使用真实语义索引；有 embedding key 时增加 dense，
    # 这样完整自然语言问题可以通过语义相似度召回资产，而不是依赖 intent_hints。
    store = SemanticBindingSearchStore(
        session,
        HybridRetrievalConfig(
            embedding_dimension=embedding_config.dimension,
            dense_enabled=embedding_provider is not None,
        ),
    )
    query_vector = (
        embedding_provider.embed_query(question)
        if embedding_provider is not None
        else None
    )
    definitions: dict[tuple[str, int], dict[str, Any]] = {}
    for asset_type, elements in (
        ("METRIC", getattr(schema, "metrics", [])),
        ("DIMENSION", getattr(schema, "dimensions", [])),
        ("VALUE", getattr(schema, "dimension_values", [])),
        ("TERM", getattr(schema, "terms", [])),
    ):
        for element in elements:
            definitions[(asset_type, int(element.id))] = element.model_dump(mode="json")

    groups: dict[str, list[dict[str, Any]]] = {
        "metrics": [],
        "dimensions": [],
        "values": [],
        "terms": [],
    }
    search_targets = {
        "metrics": RetrievalPurpose.METRIC,
        "dimensions": RetrievalPurpose.DIMENSION,
        "values": RetrievalPurpose.VALUE,
        "terms": RetrievalPurpose.TERM,
    }
    for group, purpose in search_targets.items():
        subquery = RetrievalSubQuery(
            subquery_id=f"raw:{group}",
            purpose=purpose,
            text=question,
            required=False,
        )
        by_ref: dict[tuple[str, int, int | None], dict[str, Any]] = {}
        recalls = [
            recall
            for search in (store.search_exact, store.search_alias, store.search_lexical)
            for recall in search(request, subquery, limit=20)
        ]
        if query_vector is not None:
            recalls.extend(store.search_dense(request, subquery, query_vector, limit=20))
        for recall in recalls:
            candidate = _raw_candidate_from_hit(
                recall.hit,
                definitions,
                recall_score=getattr(recall, "score", None),
            )
            if candidate is None:
                continue
            key = (
                str(candidate["asset_type"]),
                int(candidate["asset_id"]),
                candidate.get("model_id"),
            )
            existing = by_ref.get(key)
            if existing is None:
                by_ref[key] = candidate
                continue
            existing["score"] = max(existing["score"], candidate["score"])
            existing["retrieval_scores"].update(candidate["retrieval_scores"])
            if candidate.get("matched_text"):
                existing["matched_text"] = candidate["matched_text"]
            if candidate.get("matched_field"):
                existing["matched_field"] = candidate["matched_field"]
        groups[group] = sorted(
            by_ref.values(),
            key=lambda item: (-float(item["score"]), int(item["asset_id"])),
        )[:20]

    time_dimensions = [
        item
        for item in groups["dimensions"]
        if _is_time_dimension_payload(item.get("payload") or {})
    ]
    groups["dimensions"] = [
        item
        for item in groups["dimensions"]
        if not _is_time_dimension_payload(item.get("payload") or {})
    ]
    groups["time_dimensions"] = time_dimensions
    return {
        "status": "candidate_recall",
        "decision": {
            "status": "candidate_recall",
            "strategy": "raw_question",
            "reason_codes": [
                "DENSE_ENABLED" if embedding_provider is not None else "DENSE_API_KEY_MISSING"
            ],
        },
        "candidate_groups": groups,
        "selected_assets": {
            "metrics": [],
            "dimensions": [],
            "values": [],
            "terms": [],
        },
        "allowed_asset_ids": [],
        "slot_bindings": {},
    }


def _asset_ref(candidate: dict[str, Any]) -> str:
    """把候选资产转换为传给模型和校验器共用的稳定引用。"""

    asset_type = str(candidate.get("asset_type") or "").upper()
    asset_id = candidate.get("asset_id")
    model_id = candidate.get("model_id")
    if model_id in (None, ""):
        return f"{asset_type}:{asset_id}"
    return f"{asset_type}:{asset_id}:{model_id}"


def _candidate_with_ref(
    candidate: dict[str, Any],
    definitions: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    """复制候选，补充稳定 ref 和语义定义，不修改检索服务返回对象。"""

    item = dict(candidate)
    item["ref"] = _asset_ref(item)
    # 公共 candidate_groups 为控制上下文大小默认不携带 payload；测试绑定方案时
    # 必须把候选的完整语义定义补回来，否则模型只能按名称猜测指标口径。
    if "payload" not in item:
        definition = definitions.get(
            (str(item.get("asset_type") or "").upper(), int(item["asset_id"]))
        )
        if definition is not None:
            item["payload"] = definition
    return item


def build_candidate_context(
    payload: dict[str, Any],
    schema: Any,
) -> dict[str, Any]:
    """整理语义检索结果，保留候选定义、绑定建议和决策状态。"""

    definitions: dict[tuple[str, int], dict[str, Any]] = {}
    schema_groups = {
        "metrics": ("METRIC", getattr(schema, "metrics", [])),
        "dimensions": ("DIMENSION", getattr(schema, "dimensions", [])),
        "values": ("VALUE", getattr(schema, "dimension_values", [])),
        "terms": ("TERM", getattr(schema, "terms", [])),
    }
    for _, (asset_type, elements) in schema_groups.items():
        for element in elements:
            definitions[(asset_type, int(element.id))] = element.model_dump(mode="json")

    candidate_groups: dict[str, list[dict[str, Any]]] = {}
    for group in ASSET_GROUPS:
        candidate_groups[group] = [
            _candidate_with_ref(item, definitions)
            for item in payload.get("candidate_groups", {}).get(group, [])
            if isinstance(item, dict)
        ]
    candidate_groups["time_dimensions"] = [
        _candidate_with_ref(item, definitions)
        for item in payload.get("candidate_groups", {}).get("time_dimensions", [])
        if isinstance(item, dict)
    ]

    selected_assets: dict[str, list[dict[str, Any]]] = {}
    raw_selected = payload.get("selected_assets", {})
    for group in ASSET_GROUPS:
        selected_assets[group] = [
            _candidate_with_ref(item, definitions)
            for item in raw_selected.get(group, [])
            if isinstance(item, dict)
        ]

    return {
        "status": payload.get("status"),
        "decision": payload.get("decision", {}),
        "allowed_asset_ids": payload.get("allowed_asset_ids", []),
        "candidate_groups": candidate_groups,
        "selected_assets": selected_assets,
        "slot_bindings": payload.get("slot_bindings", {}),
    }


def build_model_prompt(question: str, context: dict[str, Any]) -> str:
    """构造候选资产绑定模型的输入。"""

    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    return (
        "请根据用户问题和语义层候选资产生成逻辑查询。\n\n"
        f"用户问题：\n{question}\n\n"
        "语义层候选资产上下文：\n"
        f"{context_text}"
    )


def call_model(api_key: str, question: str, context: dict[str, Any]) -> dict[str, Any]:
    """调用 Orca Chat Completions 接口，并解析模型 JSON。"""

    payload = {
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_model_prompt(question, context)},
        ],
    }
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口返回 HTTP {error.code}: {error_body}") from error
    except URLError as error:
        raise RuntimeError(f"请求 Orca 接口失败: {error.reason}") from error

    response_json = json.loads(response_body)
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"接口响应缺少 choices[0].message.content: {response_body}") from error
    if not isinstance(content, str):
        raise RuntimeError("模型返回的 message.content 不是字符串")

    try:
        result = json.loads(content.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError(f"模型没有返回合法 JSON，原始内容为：\n{content}") from error
    if not isinstance(result, dict):
        raise RuntimeError("模型输出不是 JSON 对象")
    return result


def _candidate_index(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建立 ref 到候选资产的索引，用于禁止模型越权引用资产。"""

    result: dict[str, dict[str, Any]] = {}
    for candidates in context["candidate_groups"].values():
        for candidate in candidates:
            result[str(candidate["ref"])] = candidate
    return result


def _validate_ref(
    ref: Any,
    candidates: dict[str, dict[str, Any]],
    expected_types: set[str],
    field_name: str,
) -> list[str]:
    """校验一个逻辑查询引用是否来自候选且类型正确。"""

    errors: list[str] = []
    if not isinstance(ref, str):
        return [f"{field_name}.ref 必须是字符串"]
    match = REF_PATTERN.fullmatch(ref)
    if match is None:
        errors.append(f"{field_name}.ref 格式非法：{ref}")
        return errors
    asset_type = match.group(1)
    if asset_type not in expected_types:
        errors.append(
            f"{field_name}.ref 类型错误：{asset_type}，期望 {sorted(expected_types)}"
        )
    if ref not in candidates:
        errors.append(f"{field_name}.ref 不在语义检索候选中：{ref}")
    return errors


def _list_field(
    result: dict[str, Any],
    field_name: str,
    errors: list[str],
) -> list[Any]:
    """读取模型数组字段，避免非法 null 让校验器自身崩溃。"""

    if field_name not in result:
        return []
    value = result[field_name]
    if not isinstance(value, list):
        errors.append(f"{field_name} 必须是数组，不能是 {type(value).__name__}")
        return []
    return value


def validate_logical_query(
    result: object,
    context: dict[str, Any],
) -> list[str]:
    """校验模型输出是否满足候选绑定和下游逻辑查询契约。"""

    if not isinstance(result, dict):
        return ["模型输出不是 JSON 对象"]

    errors: list[str] = []
    if set(result) != LOGICAL_QUERY_KEYS:
        errors.append(
            "顶层字段必须严格为 "
            f"{sorted(LOGICAL_QUERY_KEYS)}，实际为 {sorted(result)}"
        )

    status = result.get("status")
    if status not in {"resolved", "needs_clarification", "missed"}:
        errors.append(f"status 非法：{status!r}")

    candidates = _candidate_index(context)
    for index, item in enumerate(_list_field(result, "measures", errors)):
        if not isinstance(item, dict):
            errors.append(f"measures[{index}] 必须是对象")
            continue
        errors.extend(
            _validate_ref(item.get("ref"), candidates, {"METRIC"}, f"measures[{index}]")
        )

    for index, item in enumerate(_list_field(result, "group_by", errors)):
        if not isinstance(item, dict):
            errors.append(f"group_by[{index}] 必须是对象")
            continue
        errors.extend(
            _validate_ref(item.get("ref"), candidates, {"DIMENSION"}, f"group_by[{index}]")
        )

    for index, item in enumerate(_list_field(result, "filters", errors)):
        field_name = f"filters[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{field_name} 必须是对象")
            continue
        stage = item.get("stage")
        if stage not in {"where", "having"}:
            errors.append(f"{field_name}.stage 必须是 where 或 having")
        expected_types = {"DIMENSION"} if stage == "where" else {"METRIC"}
        errors.extend(_validate_ref(item.get("ref"), candidates, expected_types, field_name))
        if "value" not in item:
            errors.append(f"{field_name} 缺少 value")
        value_ref = item.get("value_ref")
        if value_ref is not None:
            errors.extend(_validate_ref(value_ref, candidates, {"VALUE"}, f"{field_name}.value_ref"))

    for index, item in enumerate(_list_field(result, "time_filters", errors)):
        field_name = f"time_filters[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{field_name} 必须是对象")
            continue
        errors.extend(_validate_ref(item.get("ref"), candidates, {"DIMENSION"}, field_name))
        time_refs = {
            str(candidate["ref"])
            for candidate in context["candidate_groups"].get("time_dimensions", [])
        }
        if item.get("ref") not in time_refs:
            errors.append(f"{field_name}.ref 不是检索得到的时间维度：{item.get('ref')}")
        if not isinstance(item.get("expression"), str) or not item.get("expression", "").strip():
            errors.append(f"{field_name}.expression 必须保留用户的时间表达")

    for index, item in enumerate(_list_field(result, "order_by", errors)):
        field_name = f"order_by[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{field_name} 必须是对象")
            continue
        errors.extend(
            _validate_ref(item.get("ref"), candidates, {"METRIC", "DIMENSION"}, field_name)
        )
        if item.get("direction") not in {"asc", "desc"}:
            errors.append(f"{field_name}.direction 必须是 asc 或 desc")

    limit = result.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        errors.append("limit 必须是正整数或 null")

    if not isinstance(result.get("calculations"), list):
        errors.append("calculations 必须是数组")
    if not isinstance(result.get("unresolved"), list):
        errors.append("unresolved 必须是数组")

    retrieval_status = str(context.get("status") or "")
    if retrieval_status == "missed" and status == "resolved":
        errors.append("语义检索已 missed，模型不能输出 resolved")
    if status == "missed" and any(
        result.get(key) for key in ("measures", "group_by", "filters", "time_filters", "order_by")
    ):
        errors.append("status=missed 时不能继续输出资产引用")
    if status == "resolved" and result.get("unresolved"):
        errors.append("status=resolved 时 unresolved 必须为空数组")
    return errors


def validate_expected_bindings(
    result: dict[str, Any],
    context: dict[str, Any],
    expected: dict[str, set[str]],
) -> list[str]:
    """按业务标识校验模型绑定结果，避免只校验 ref 格式。"""

    candidates = _candidate_index(context)

    def refs_from_field(field_name: str, ref_key: str = "ref") -> set[str]:
        values = result.get(field_name)
        if not isinstance(values, list):
            return set()
        return {
            str(item.get(ref_key))
            for item in values
            if isinstance(item, dict) and item.get(ref_key)
        }

    def biz_names(refs: set[str]) -> set[str]:
        return {
            str(candidates[ref].get("biz_name"))
            for ref in refs
            if ref in candidates and candidates[ref].get("biz_name")
        }

    errors: list[str] = []
    field_sources = {
        "measures": refs_from_field("measures"),
        "group_by": refs_from_field("group_by"),
        "filters": refs_from_field("filters"),
        "time_filters": refs_from_field("time_filters"),
    }
    for field_name, actual_refs in field_sources.items():
        expected_names = expected.get(field_name, set())
        actual_names = biz_names(actual_refs)
        if actual_names != expected_names:
            errors.append(
                f"{field_name} 业务绑定错误：期望 {sorted(expected_names)}，"
                f"实际 {sorted(actual_names)}"
            )

    expected_calculations = expected.get("calculations")
    if expected_calculations is not None:
        actual_calculations = {
            str(item.get("type"))
            for item in result.get("calculations", [])
            if isinstance(item, dict) and item.get("type")
        }
        if actual_calculations != expected_calculations:
            errors.append(
                f"calculations 类型错误：期望 {sorted(expected_calculations)}，"
                f"实际 {sorted(actual_calculations)}"
            )
    return errors


def validate_expected_candidates(
    context: dict[str, Any],
    expected: dict[str, set[str]],
) -> list[str]:
    """校验用户所需资产是否已经进入候选，单独暴露召回失败。"""

    candidate_groups = context.get("candidate_groups", {})

    def available_names(group_name: str) -> set[str]:
        return {
            str(candidate.get("biz_name"))
            for candidate in candidate_groups.get(group_name, [])
            if isinstance(candidate, dict) and candidate.get("biz_name")
        }

    expected_groups = {
        "measures": "metrics",
        "group_by": "dimensions",
        "filters": "dimensions",
        "time_filters": "time_dimensions",
    }
    errors: list[str] = []
    for field_name, group_name in expected_groups.items():
        missing = expected.get(field_name, set()) - available_names(group_name)
        if missing:
            errors.append(
                f"候选召回缺失 {field_name}：{sorted(missing)}，"
                f"候选组为 {group_name}"
            )
    return errors


def print_json(title: str, value: Any) -> None:
    """以便于人工检查的格式输出 JSON。"""

    print(f"\n--- {title} ---")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def summarize_candidate_context(context: dict[str, Any]) -> dict[str, Any]:
    """只打印候选摘要；模型仍然接收完整的候选定义。"""

    groups: dict[str, list[dict[str, Any]]] = {}
    for group, candidates in context.get("candidate_groups", {}).items():
        groups[group] = [
            {
                "ref": candidate.get("ref"),
                "name": candidate.get("name"),
                "biz_name": candidate.get("biz_name"),
                "score": candidate.get("score"),
                "matched_text": candidate.get("matched_text"),
                "matched_field": candidate.get("matched_field"),
            }
            for candidate in candidates
        ]
    return {
        "status": context.get("status"),
        "decision": context.get("decision"),
        "candidate_groups": groups,
    }


def parse_args() -> argparse.Namespace:
    """解析脚本参数。"""

    parser = argparse.ArgumentParser(description="测试语义检索候选约束下的逻辑查询绑定")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--actor-id", type=int, default=1)
    # 243 是 P1 问数真实测试使用的商城店铺数据集（stall_dataset）。
    parser.add_argument("--dataset-id", type=int, default=243)
    parser.add_argument("--case", dest="case_name", choices=[item["name"] for item in TEST_CASES])
    parser.add_argument("--api-key", default=os.getenv("ORCA_API_KEY"))
    parser.add_argument(
        "--disable-rerank",
        action="store_true",
        help="兼容旧命令；自然语言直接检索路径本身不调用 reranker",
    )
    return parser.parse_args()


def run_case(
    case: dict[str, Any],
    *,
    tenant_id: int,
    actor_id: int,
    dataset_id: int,
    api_key: str,
) -> bool:
    """执行一个“检索 -> 模型绑定 -> 校验”案例。"""

    print(f"\n\n================ {case['name']} ================")
    print(f"问题：{case['question']}")
    print("检索路径：自然语言问题 -> 指标/维度/值候选检索 -> 模型绑定")
    with Session(engine) as session:
        schema = build_semantic_schema_service(session).build_dataset_schema(
            tenant_id,
            dataset_id,
        )
        retrieval_payload = retrieve_by_raw_question(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            dataset_id=dataset_id,
            question=case["question"],
            schema=schema,
        )

    context = build_candidate_context(retrieval_payload, schema)
    print_json("语义检索候选摘要", summarize_candidate_context(context))
    logical_query = call_model(api_key, case["question"], context)
    print_json("模型逻辑查询", logical_query)
    errors = validate_expected_candidates(context, case.get("expected", {}))
    errors.extend(validate_logical_query(logical_query, context))
    errors.extend(
        validate_expected_bindings(
            logical_query,
            context,
            case.get("expected", {}),
        )
    )
    if errors:
        print_json("校验错误", errors)
        print("结果：FAIL")
        return False
    print("结果：PASS")
    return True


def main() -> int:
    """脚本入口。"""

    args = parse_args()
    # if not args.api_key:
    #     raise SystemExit("缺少 Orca API Key，请先设置 ORCA_API_KEY 或传入 --api-key")

    cases = [
        item for item in TEST_CASES
        if args.case_name is None or item["name"] == args.case_name
    ]
    passed = 0
    for case in cases:
        if run_case(
            case,
            tenant_id=args.tenant_id,
            actor_id=args.actor_id,
            dataset_id=args.dataset_id,
            api_key="sk-3c434fdbc6c243d2849eb9d1cdc5aceb",
        ):
            passed += 1

    print(f"\n汇总：{passed}/{len(cases)} 通过")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
