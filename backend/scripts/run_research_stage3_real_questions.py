"""Research 第三阶段真实问题端到端验收（真实 Policy + 真实数据源）。

走生产入口 ``create_agent_start_events``：问题重写、候选检索、语义解析、模式路由、
Research 动态闭环、子计划 PROVEN 执行和报告收口全部使用真实组件；
唯一的环境前置是 ``CHAT_AGENT_EXECUTION_MODES`` 需要包含 research。

用例覆盖 doc §第三阶段真实问题集：
1. 层级下钻（商家 -> 档口）；
2. 贡献度（各档口对 GMV 下降的贡献，含对账）；
3. 驱动验证（订单量 vs 客单价假设）；
4. 多方向并行；
5. 治理能力不足（渠道类型无 CONTRIBUTION 能力时的显式拒绝或受限收口）;
6. 越界请求（商品维度不在目标模型治理范围内）；
7. 报告约束（每条发现必须引用当前 Run 的 Evidence）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# 必须在导入任何应用模块前设置：Settings 在导入时实例化。
os.environ["CHAT_AGENT_ENABLED"] = "true"
os.environ["CHAT_AGENT_EXECUTION_MODES"] = "fast,plan,research"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlmodel import Session  # noqa: E402

from apps.chatbi.composition import (  # noqa: E402
    build_chat_application_service,
    configure_agent_cleanup,
)
from apps.chatbi.models.dto.agent import AgentStartStreamRequest  # noqa: E402
from apps.chatbi.orchestration.agent.service import (  # noqa: E402
    create_agent_start_events,
)
from apps.chatbi.repository.sqlmodel.agent_run_repository import (  # noqa: E402
    AgentExecutionDeletionService,
)
from apps.conversation import CreateChat  # noqa: E402
from common.core.db import engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

STALL_DIM_REF = "DIMENSION:278:246"  # 档口 stall_id（逻辑维度 14）
SELLER_DIM_REF = "DIMENSION:277:246"  # 商家 seller_id（逻辑维度 2）
TOTAL_ORDER_METRIC_REF = "METRIC:265:246"
AOV_METRIC_REF = "METRIC:303:246"
CHANNEL_DIM_REFS = ("DIMENSION:309:249",)  # 渠道：属于模型 249，非 GMV 目标模型
GOODS_DIM_REFS = ("DIMENSION:297:248", "DIMENSION:298:248")  # 商品/货号：模型 248


@dataclass(frozen=True, slots=True)
class ResearchCase:
    """一个真实问题及其验收断言。"""

    name: str
    question: str
    expected_mode: str = "research"
    # 允许的最终状态；治理拒绝路径允许 FAILED（显式错误）。
    allowed_statuses: tuple[str, ...] = ("succeeded",)
    allowed_finish_reasons: tuple[str, ...] = ()
    # 至少一条证据覆盖这些维度引用（下钻/贡献类用例）。
    require_dimension_refs: tuple[str, ...] = ()
    # 断言证据中不出现的维度（越界/治理缺失用例）。
    forbid_dimension_refs: tuple[str, ...] = ()
    require_hypotheses: bool = False
    require_hierarchy: bool = False
    require_contribution: bool = False
    require_parallel: bool = False
    require_driver_metric_refs: tuple[str, ...] = ()
    # 路由或执行层显式拒绝时，run.error 命中这些代码同样算验收通过。
    allowed_error_codes: tuple[str, ...] = ()


CASES: tuple[ResearchCase, ...] = (
    ResearchCase(
        name="hierarchy_drilldown",
        question=(
            "2026年6月29日总GMV比6月28日下降了，"
            "请找出下降最大的商家，然后继续下钻分析该商家下"
            "各档口的下降情况。"
        ),
        # 下钻对象依赖中间结果（result_driven_dimension），
        # 且必须严格沿已认证层级 商家 -> 档口 执行。
        require_dimension_refs=(STALL_DIM_REF, SELLER_DIM_REF),
        require_hierarchy=True,
    ),
    ResearchCase(
        name="contribution",
        question=(
            "总GMV在6月29日比6月28日明显下降了，帮我找出主要原因，"
            "需要的话可以量化各档口对这次下降的贡献。"
        ),
        # 开放式原因探索中由 Policy 选择 contribution 动作，
        # 服务端强制执行 scope 内贡献度对账。
        require_dimension_refs=(STALL_DIM_REF,),
        require_contribution=True,
    ),
    ResearchCase(
        name="driver_validation",
        question=(
            "2026年6月29日总GMV比6月28日下降了。"
            "请分别验证总订单数减少和订单平均客单价下降这两个方向是否得到"
            "数据支持，并给出证据；不要把同向变化直接表述为因果。"
        ),
        # 验证方向依赖证据：Policy 需要创建假设并用治理驱动关系验证。
        require_hypotheses=True,
        require_driver_metric_refs=(TOTAL_ORDER_METRIC_REF, AOV_METRIC_REF),
    ),
    ResearchCase(
        name="parallel_directions",
        question=(
            "2026年6月29日总GMV比6月28日下降了，原因是什么？"
            "请同时从商家和档口两个方向展开分析，找出下降最集中的对象。"
        ),
        # 两个独立方向同轮并行，停止条件依赖数据。
        require_dimension_refs=(STALL_DIM_REF, SELLER_DIM_REF),
        require_parallel=True,
    ),
    ResearchCase(
        name="governance_missing_channel",
        question=(
            "2026年6月29日总GMV比6月28日下降了，"
            "请分析各交易渠道对这次下降的贡献分别是多少。"
        ),
        # 渠道维度属于模型 249：期望路由层显式拒绝（DIMENSION_MODEL_MISMATCH），
        # 或 Research 在治理范围内收口且绝不产出渠道维度的贡献证据。
        allowed_statuses=("succeeded", "failed", "partial"),
        forbid_dimension_refs=CHANNEL_DIM_REFS,
        allowed_error_codes=(
            "DIMENSION_MODEL_MISMATCH",
            "SEMANTIC_EXECUTION_ASSET_NOT_COVERED",
        ),
    ),
    ResearchCase(
        name="out_of_scope_goods",
        question=(
            "2026年6月29日总GMV比6月28日下降了，"
            "请分析原因并继续下钻到商品ID，找出哪些商品导致下降。"
        ),
        # 商品/货号层级未纳入目标指标治理范围：必须显式拒绝。
        allowed_statuses=("failed",),
        forbid_dimension_refs=GOODS_DIM_REFS,
        allowed_error_codes=("DIMENSION_MODEL_MISMATCH", "ACTION_NOT_ALLOWED"),
    ),
)


@dataclass(slots=True)
class CaseResult:
    name: str
    ok: bool
    failures: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def _latest_run(session: Session, chat_id: int) -> dict[str, Any]:
    row = session.exec(
        text(
            "SELECT r.id, r.record_id, r.status, r.execution_mode, "
            "r.derived_state, r.error_class, r.error "
            "FROM chatbi_agent_run r WHERE r.chat_id = :chat_id "
            "ORDER BY r.id DESC LIMIT 1"
        ).bindparams(chat_id=chat_id)
    ).mappings().one()
    return dict(row)


def _create_case_chat(
    session: Session,
    case: ResearchCase,
    args: argparse.Namespace,
) -> int:
    """通过生产会话服务为单个验收问题创建独立会话。"""

    configure_agent_cleanup(AgentExecutionDeletionService)
    chat = build_chat_application_service(session).create(
        user_id=args.user_id,
        workspace_id=args.tenant_id,
        request=CreateChat(
            dataset_id=243,
            datasource=args.datasource_id,
            question=f"Research 第三阶段验收：{case.name}",
        ),
    )
    if chat.id is None:
        raise RuntimeError(f"验收会话创建成功但未返回 ID: case={case.name}")
    return int(chat.id)


def _run_case(
    case: ResearchCase,
    args: argparse.Namespace,
    *,
    chat_id: int,
) -> CaseResult:
    print(f"\n{'=' * 88}\n[case:{case.name}] {case.question}\n{'=' * 88}")
    user = SimpleNamespace(id=args.user_id, oid=args.tenant_id)
    request = AgentStartStreamRequest(
        chat_id=chat_id,
        question=case.question,
        datasource_id=args.datasource_id,
    )
    event_count = 0
    tail: list[str] = []
    for event in create_agent_start_events(user, request):
        event_count += 1
        payload = str(event)
        tail.append(payload[:300])
        del payload
    print(f"[case:{case.name}] events={event_count}")

    with Session(engine) as session:
        run = _latest_run(session, chat_id)
    derived = run.get("derived_state") or {}
    research_state = derived.get("research_state") or {}
    evidence = derived.get("research_evidence") or []
    report = research_state.get("report") or {}

    result = CaseResult(name=case.name, ok=True)
    detail = {
        "run_id": run.get("id"),
        "record_id": run.get("record_id"),
        "run_status": run.get("status"),
        "execution_mode": run.get("execution_mode"),
        "error": run.get("error"),
        "research_status": research_state.get("status"),
        "finish_reason": research_state.get("finish_reason"),
        "premise_supported": research_state.get("premise_supported"),
        "evidence_count": len(evidence),
        "hypotheses": research_state.get("hypotheses"),
        "iterations": len(research_state.get("iteration_records") or ()),
        "covered_dimension_refs": research_state.get("covered_dimension_refs"),
        "covered_driver_metric_refs": research_state.get("covered_driver_metric_refs"),
        "covered_hierarchy_ids": research_state.get("covered_hierarchy_ids"),
        "covered_contribution_dimension_refs": research_state.get(
            "covered_contribution_dimension_refs"
        ),
        "report_summary": report.get("summary"),
        "findings": [
            {
                "statement": f.get("statement"),
                "evidence_ids": list(f.get("evidence_ids") or ()),
                "claim_level": f.get("claim_level"),
            }
            for f in report.get("findings") or ()
        ],
        "limitations": report.get("limitations"),
    }
    result.detail = detail
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))

    def fail(message: str) -> None:
        result.ok = False
        result.failures.append(message)

    error_text = json.dumps(run.get("error"), ensure_ascii=False, default=str)
    hit_error_code = next(
        (code for code in case.allowed_error_codes if code in error_text),
        None,
    )
    if hit_error_code is None:
        if run.get("execution_mode") != case.expected_mode:
            fail(
                f"mode mismatch: expected {case.expected_mode}, "
                f"got {run.get('execution_mode')}"
            )

    status = research_state.get("status")
    if hit_error_code is not None:
        print(
            f"[case:{case.name}] 治理显式拒绝路径: error_code={hit_error_code} "
            f"run_status={run.get('status')}"
        )
    elif status not in case.allowed_statuses:
        fail(f"research status {status} not in {case.allowed_statuses}")
    finish_reason = research_state.get("finish_reason")
    if case.allowed_finish_reasons and finish_reason not in case.allowed_finish_reasons:
        fail(f"finish_reason {finish_reason} not in {case.allowed_finish_reasons}")

    evidence_ids = {item.get("evidence_id") for item in evidence}
    if hit_error_code is None:
        dimension_hits = {
            ref
            for item in evidence
            for ref in (item.get("dimension_refs") or ())
            if ref in set(case.require_dimension_refs)
        }
        missing_dims = set(case.require_dimension_refs) - dimension_hits
        if missing_dims:
            fail(f"evidence missing required dimension refs: {sorted(missing_dims)}")
    forbidden_hits = {
        ref
        for item in evidence
        for ref in (item.get("dimension_refs") or ())
        if ref in set(case.forbid_dimension_refs)
    }
    if forbidden_hits:
        fail(f"governance violation: evidence contains {sorted(forbidden_hits)}")

    if case.require_hypotheses and hit_error_code is None:
        hypotheses = research_state.get("hypotheses") or []
        if not hypotheses:
            fail("expected at least one hypothesis")
        terminal = {
            item.get("id")
            for item in hypotheses
            if item.get("status") in {"supported", "weakened", "inconclusive"}
            and item.get("evidence_ids")
        }
        if not terminal:
            fail("expected at least one evidence-backed terminal hypothesis")

    if case.require_hierarchy and hit_error_code is None:
        hierarchy_evidence = [
            item
            for item in evidence
            if STALL_DIM_REF in (item.get("dimension_refs") or ())
            and any(
                applied.get("target_ref") == SELLER_DIM_REF
                for applied in (item.get("applied_filters") or ())
            )
        ]
        if not hierarchy_evidence:
            fail("expected adjacent seller-to-stall drilldown with inherited filter")
        if not (research_state.get("covered_hierarchy_ids") or []):
            fail("research state did not record completed hierarchy")

    if case.require_contribution and hit_error_code is None:
        contribution_evidence = [
            item
            for item in evidence
            if STALL_DIM_REF in (item.get("dimension_refs") or ())
            and any(
                column.get("value_role") == "contribution"
                for column in (item.get("logical_columns") or ())
            )
        ]
        if not contribution_evidence:
            fail("expected reconciled contribution evidence")
        if STALL_DIM_REF not in set(
            research_state.get("covered_contribution_dimension_refs") or ()
        ):
            fail("research state did not record completed contribution dimension")

    if case.require_driver_metric_refs and hit_error_code is None:
        covered_drivers = set(research_state.get("covered_driver_metric_refs") or ())
        missing_drivers = set(case.require_driver_metric_refs) - covered_drivers
        if missing_drivers:
            fail(f"driver validation missing metrics: {sorted(missing_drivers)}")
        for driver_ref in case.require_driver_metric_refs:
            if not any(
                driver_ref in (item.get("metric_refs") or ())
                and item.get("hypothesis_ids")
                for item in evidence
            ):
                fail(f"driver {driver_ref} lacks hypothesis evidence")

    if case.require_parallel and hit_error_code is None:
        batch_ids = {
            item.get("batch_id")
            for item in evidence
            if set(item.get("dimension_refs") or ())
            & {STALL_DIM_REF, SELLER_DIM_REF}
            and item.get("batch_id")
        }
        shared_batch = any(
            {
                ref
                for item in evidence
                if item.get("batch_id") == batch_id
                for ref in (item.get("dimension_refs") or ())
            }
            >= {STALL_DIM_REF, SELLER_DIM_REF}
            for batch_id in batch_ids
        )
        if not shared_batch:
            fail("seller and stall directions were not executed in the same batch")

    # 报告约束：每条发现的引用都必须属于当前 Run 的 Evidence。
    for finding in report.get("findings") or []:
        dangling = set(finding.get("evidence_ids") or ()) - evidence_ids
        if dangling:
            fail(f"finding cites unknown evidence: {sorted(dangling)}")
        if not finding.get("evidence_ids"):
            fail("finding without evidence citation")

    if result.ok:
        print(f"[case:{case.name}] PASS")
    else:
        print(f"[case:{case.name}] FAIL: {result.failures}")
        for line in tail[-5:]:
            print(f"  event-tail: {line}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--datasource-id", type=int, default=13)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "data" / "research_stage3_results.json",
    )
    args = parser.parse_args()

    if args.list_cases:
        for case in CASES:
            print(f"{case.name}: {case.question}")
        return 0

    selected = set(args.case)
    cases = [case for case in CASES if not selected or case.name in selected]
    if not cases:
        raise SystemExit(f"没有选中的用例: {selected}")

    results = []
    for case in cases:
        try:
            # 每个真实问题使用独立会话，避免历史问题参与重写和语义理解。
            with Session(engine) as session:
                chat_id = _create_case_chat(session, case, args)
            results.append(_run_case(case, args, chat_id=chat_id))
        except Exception as exc:  # 单个用例失败不阻断其余用例
            failing = CaseResult(name=case.name, ok=False, failures=[repr(exc)])
            results.append(failing)
            print(f"[case:{case.name}] EXCEPTION: {exc!r}")

    passed = sum(1 for item in results if item.ok)
    print(f"\n{'=' * 88}\n验收结果: {passed}/{len(results)} passed")
    for item in results:
        mark = "PASS" if item.ok else "FAIL"
        print(f"  [{mark}] {item.name}" + ("" if item.ok else f": {item.failures}"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            [
                {
                    "name": item.name,
                    "ok": item.ok,
                    "failures": item.failures,
                    "detail": item.detail,
                }
                for item in results
            ],
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"明细已写入 {args.output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
