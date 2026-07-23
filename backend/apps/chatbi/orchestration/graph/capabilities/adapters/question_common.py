"""Graph 问题节点共享的模型协议与提示词辅助函数。"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from apps.chatbi.models import QuestionModelResponse


@dataclass(frozen=True)
class QuestionClassificationPrompt:
    """问题分类模型提示词。"""

    system_prompt: str
    user_prompt: str


class QuestionClassificationModelClient(Protocol):
    """问题分类模型客户端协议，便于测试中替换真实大模型。"""

    def __call__(self, prompt: QuestionClassificationPrompt) -> str: ...


class CallableQuestionModelClient:
    """把 Graph 现有可调用模型端口适配到 ChatBI 统一端口。"""

    def __init__(self, client: QuestionClassificationModelClient) -> None:
        self._client = client

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> QuestionModelResponse:
        content = self._client(
            QuestionClassificationPrompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        )
        return QuestionModelResponse(content=content)


def markdown_user_prompt(
    *,
    rewritten_question: str,
    task: str,
    available_dimensions: list[dict[str, Any]] | None = None,
    time_dimensions: list[dict[str, Any]] | None = None,
    subject_domains: list[dict[str, Any]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    user_feedback: dict[str, Any] | None = None,
) -> str:
    parts = ["# 用户问题", rewritten_question]
    if available_dimensions is not None:
        parts.extend(["# 可用维度", _json_block(available_dimensions)])
    if time_dimensions is not None:
        parts.extend(["# 时间字段候选", _json_block(time_dimensions)])
    if subject_domains is not None:
        parts.extend(["# 候选主题域", _json_block(subject_domains)])
    parts.extend(
        [
            "# 会话上下文",
            _json_block(conversation_context or {}),
            "# 用户反馈",
            _json_block(user_feedback or {}),
            "# 任务",
            task,
        ]
    )
    return "\n\n".join(parts).strip()


def _json_block(value: Any) -> str:
    return (
        "```json\n"
        + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```"
    )


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def default_subject_domain(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "domain_id": None,
        "domain_name": None,
        "domain_biz_name": None,
        "confidence": 0.0,
        "reason": "",
        "candidate_domain_ids": [],
    }


def subject_domain_payload(
    candidate: dict[str, Any],
    status: str,
    confidence: float,
    reason: str,
    candidate_domain_ids: list[int] | None = None,
) -> dict[str, Any]:
    domain_id = candidate["domain_id"]
    return {
        "status": status,
        "domain_id": domain_id,
        "domain_name": candidate["name"],
        "domain_biz_name": candidate["biz_name"],
        "confidence": confidence,
        "reason": reason,
        "candidate_domain_ids": candidate_domain_ids or [domain_id],
    }


def valid_candidate_domain_ids(
    value: Any,
    candidate_by_id: dict[int, dict[str, Any]],
) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        domain_id = int_or_none(item)
        if (
            domain_id is not None
            and domain_id in candidate_by_id
            and domain_id not in result
        ):
            result.append(domain_id)
    return result


def normalize_subject_domain_candidates(
    subject_domains: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in subject_domains:
        if not isinstance(item, dict):
            continue
        domain_id = int_or_none(item.get("domain_id") or item.get("id"))
        if domain_id is None or domain_id in seen:
            continue
        seen.add(domain_id)
        name = str(item.get("name") or item.get("domain_name") or domain_id)
        biz_name = str(
            item.get("biz_name") or item.get("domain_biz_name") or domain_id
        )
        candidates.append(
            {
                "domain_id": domain_id,
                "name": name,
                "biz_name": biz_name,
                "description": item.get("description"),
                "model_ids": [
                    model_id
                    for model_id in (
                        int_or_none(value)
                        for value in item.get("model_ids") or []
                    )
                    if model_id is not None
                ],
            }
        )
    return candidates

