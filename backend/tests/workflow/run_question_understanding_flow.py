from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

class StableQuestionModelClient:
    def __call__(self, prompt):
        system_prompt = getattr(prompt, "system_prompt", "")
        if "只做问题分类" in system_prompt:
            return json.dumps(
                {
                    "category": "data",
                    "reason": "用户在查询数据指标，属于 ChatBI 数据分析问题。",
                    "risk_level": "low",
                    "confidence": 0.94,
                },
                ensure_ascii=False,
            )
        if "rewritten_question" in system_prompt:
            return json.dumps(
                {
                    "rewritten_question": "今日店铺流量",
                    "need_user_input": False,
                    "missing_slots": [],
                    "image_profile_hint": None,
                },
                ensure_ascii=False,
            )
        if "分析形态识别器" in system_prompt:
            return json.dumps(
                {
                    "intent_type": "metric_query",
                    "confidence": 0.91,
                    "required_slot_types": ["metric"],
                    "query_shape": {"select_mode": "aggregate"},
                    "subject_domain": {"status": "not_required"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            )
        if "指标和时间线索识别器" in system_prompt or "指标线索和时间线索识别器" in system_prompt:
            return json.dumps(
                {
                    "metric_mentions": ["流量"],
                    "time_mentions": ["今日"],
                    "time_range": {"raw": "今日", "value_status": "provided"},
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            )
        if "维度槽位识别器" in system_prompt:
            return json.dumps(
                {
                    "dimension_mentions": [],
                    "dimension_slots": [],
                    "residual_filter_mentions": [],
                    "ambiguous_slots": [],
                    "conflict_slots": [],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"未识别的提示词: {system_prompt[:120]}")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _base_request(question: str, dataset_id: int, tenant_id: int, user_id: int) -> dict[str, Any]:
    return {
        "request": {
            "question": question,
            "dataset_id": dataset_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
        },
        "conversation": {},
        "variables": {},
        "inputs": {},
    }


def _snapshot_node_input(node_input: dict[str, Any]) -> dict[str, Any]:
    # 节点输入日志必须冻结当时的上下文，避免后续节点写入 variables 后污染历史输入。
    return copy.deepcopy({
        "node_name": node_input["node_name"],
        "request": node_input["request"],
        "conversation": node_input["conversation"],
        "variables": node_input["variables"],
        "inputs": node_input["inputs"],
    })


def run_question_understanding_flow(
    question: str,
    dataset_id: int = 3,
    tenant_id: int = 1,
    user_id: int = 1,
) -> dict[str, Any]:
    from apps.chatbi.orchestration.graph.capabilities.adapters.question import (
        QuestionAdapter,
    )

    adapter = QuestionAdapter(model_client=StableQuestionModelClient())
    context = _base_request(question, dataset_id, tenant_id, user_id)
    logs: list[dict[str, Any]] = []

    classify_input = {**context, "node_name": "classify_question"}
    classify_input_snapshot = _snapshot_node_input(classify_input)
    classification = adapter.classify(classify_input)
    context["variables"]["classification"] = classification
    logs.append(
        {
            "node": "classify_question",
            "handler": "question.classify",
            "status": "succeeded",
            "input": classify_input_snapshot,
            "output": classification,
            "route": {
                "condition": "question.data_or_followup",
                "matched": classification["category"] in {"data", "followup"},
                "reason_code": "QUESTION_DATA_OR_FOLLOWUP",
                "next_node": "rewrite_question",
            },
        }
    )

    rewrite_input = {**context, "node_name": "rewrite_question"}
    rewrite_input_snapshot = _snapshot_node_input(rewrite_input)
    rewrite = adapter.rewrite(rewrite_input)
    context["variables"]["rewrite"] = rewrite
    logs.append(
        {
            "node": "rewrite_question",
            "handler": "question.rewrite",
            "status": "succeeded",
            "input": rewrite_input_snapshot,
            "output": rewrite,
            "route": {
                "condition": "rewrite.need_user_input",
                "matched": bool(rewrite["need_user_input"]),
                "reason_code": "REWRITE_READY",
                "next_node": "recognize_intent",
            },
        }
    )

    intent_input = {**context, "node_name": "recognize_intent"}
    intent_input_snapshot = _snapshot_node_input(intent_input)
    intent = adapter.recognize_intent(intent_input)
    context["variables"]["intent"] = intent
    needs_intent_clarification = bool(intent["ambiguous_slots"] or intent["conflict_slots"] or intent["confidence"] < 0.8)
    logs.append(
        {
            "node": "recognize_intent",
            "handler": "intent.recognize",
            "status": "succeeded",
            "input": intent_input_snapshot,
            "output": intent,
            "route": {
                "condition": "intent.ambiguous",
                "matched": needs_intent_clarification,
                "reason_code": "INTENT_READY",
                "next_node": "retrieve_knowledge",
            },
        }
    )

    return {
        "title": "ChatBI v1 question understanding focused flow",
        "input": context["request"],
        "scope": {
            "start_node": "classify_question",
            "end_node": "recognize_intent",
            "excluded_nodes": [
                "ask_rewrite_clarification",
                "ask_intent_clarification",
                "retrieve_knowledge",
                "generate_sql",
                "execute_sql",
            ],
            "branch_policy": "本测试固定模型输出为 data、need_user_input=false、intent 无歧义，因此不走分叉节点。",
        },
        "nodes": logs,
        "final_context_variables": context["variables"],
    }


def print_flow_log(flow: dict[str, Any]) -> None:
    print("=== ChatBI v1 问题理解链路测试 ===")
    print("输入:")
    print(_json(flow["input"]))
    print("\n范围:")
    print(_json(flow["scope"]))
    print("\n节点执行日志:")
    for index, node in enumerate(flow["nodes"], start=1):
        print(f"\n#{index} {node['node']} | {node['handler']} | {node['status']}")
        print("输入:")
        print(_json(node["input"]))
        print("输出:")
        print(_json(node["output"]))
        print("流转:")
        print(_json(node["route"]))
    print("\n最终上下文变量:")
    print(_json(flow["final_context_variables"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只测试 ChatBI v1 的问题分类、问题重写、意图识别链路。")
    parser.add_argument("question", nargs="?", default="今日店铺流量")
    parser.add_argument("--dataset-id", type=int, default=3)
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args(argv)

    flow = run_question_understanding_flow(
        question=args.question,
        dataset_id=args.dataset_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
    )
    print_flow_log(flow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
